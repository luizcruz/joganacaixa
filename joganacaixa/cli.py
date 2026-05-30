import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from .compression import Algorithm, compress, extract, extract_from_stream, sha256_file
from .config import build_backends, get_algorithm, get_exclude_patterns, get_retries, get_zstd_level, load_config
from .manifest import Manifest, build_manifest
from .reliability import retry

console = Console()


def _try_upload(backend, path: Path, key: str) -> None:
    try:
        backend.upload(path, key)
    except Exception:
        pass


def _race_download_stream(backends, key: str):
    """Return a readable stream from the first backend that responds."""
    import threading

    result: list = []
    done = threading.Event()

    def attempt(b):
        try:
            stream = b.download_stream(key)
            if not done.is_set():
                done.set()
                result.append(stream)
        except Exception:
            pass

    threads = [threading.Thread(target=attempt, args=(b,), daemon=True) for b in backends]
    for t in threads:
        t.start()
    done.wait(timeout=30)
    return result[0] if result else None


@click.group()
@click.option("--config", "-c", type=click.Path(path_type=Path), help="Path to config file")
@click.pass_context
def main(ctx: click.Context, config: Path | None) -> None:
    """Joga na caixa — multi-cloud backup with robust compression."""
    ctx.ensure_object(dict)
    ctx.obj["config"] = load_config(config)


@main.command()
@click.argument("source", type=click.Path(exists=True, path_type=Path), default=".")
@click.option(
    "--algorithm", "-a",
    type=click.Choice([a.value for a in Algorithm]),
    help="Compression algorithm (overrides config)",
)
@click.pass_context
def store(ctx: click.Context, source: Path, algorithm: str | None) -> None:
    """Compress SOURCE and upload to all configured storage backends in parallel."""
    config = ctx.obj["config"]
    backends = build_backends(config)
    if not backends:
        console.print("[red]No storage backends configured. See config.example.yaml.[/red]")
        raise SystemExit(1)

    alg = Algorithm(algorithm) if algorithm else get_algorithm(config)
    staging = Path(config["staging_dir"])
    manifest_dir = Path(config["manifest_dir"])
    staging.mkdir(exist_ok=True)
    attempts = get_retries(config)
    level = get_zstd_level(config)

    package_id = str(int(time.time()))
    console.print(f"[cyan]Compressing {source} with {alg.value}...[/cyan]")
    archive = compress(source, staging / package_id, alg, get_exclude_patterns(config), level=level)
    size_kb = archive.stat().st_size / 1024
    checksum = sha256_file(archive)
    console.print(f"[green]Archive ready:[/green] {archive.name} ({size_kb:.1f} KB) sha256:{checksum[:12]}…")

    locations: list[str] = []
    console.print(f"[cyan]Uploading to {len(backends)} backend(s) in parallel...[/cyan]")
    with ThreadPoolExecutor(max_workers=len(backends)) as pool:
        futures = {
            pool.submit(retry, lambda b=b: b.upload(archive, archive.name), attempts): b
            for b in backends
        }
        for future in as_completed(futures):
            backend = futures[future]
            try:
                uri = future.result()
                locations.append(uri)
                console.print(f"  [green]✓[/green] {backend.name}")
            except Exception as exc:
                console.print(f"  [red]✗[/red] {backend.name}: {exc}")

    if not locations:
        archive.unlink(missing_ok=True)
        console.print("[red]All uploads failed. Archive deleted.[/red]")
        raise SystemExit(1)

    manifest = build_manifest(package_id, archive, alg, locations, checksum=checksum)
    manifest_path = manifest.save(manifest_dir)

    # Back up manifest JSON to all backends
    manifest_key = f"{package_id}.manifest.json"
    with ThreadPoolExecutor(max_workers=len(backends)) as pool:
        pool.map(lambda b: _try_upload(b, manifest_path, manifest_key), backends)

    archive.unlink()
    console.print(
        f"[green]Done.[/green] Package [cyan]{package_id}[/cyan] "
        f"({len(manifest.files)} files, {len(locations)}/{len(backends)} backends)"
    )


@main.command("list")
@click.pass_context
def list_packages(ctx: click.Context) -> None:
    """List all stored packages."""
    config = ctx.obj["config"]
    manifests = Manifest.all(Path(config["manifest_dir"]))

    if not manifests:
        console.print("[yellow]No packages found.[/yellow]")
        return

    table = Table(title="Stored packages", show_lines=True)
    table.add_column("Package ID", style="cyan")
    table.add_column("Created at")
    table.add_column("Alg")
    table.add_column("Files", justify="right")
    table.add_column("Locations")
    for m in manifests:
        table.add_row(
            m.package_id,
            m.created_at,
            m.algorithm,
            str(len(m.files)),
            "\n".join(m.locations),
        )
    console.print(table)


@main.command()
@click.argument("package_id")
@click.pass_context
def contents(ctx: click.Context, package_id: str) -> None:
    """List files inside PACKAGE_ID."""
    config = ctx.obj["config"]
    try:
        manifest = Manifest.load(package_id, Path(config["manifest_dir"]))
    except FileNotFoundError:
        console.print(f"[red]Package {package_id!r} not found.[/red]")
        raise SystemExit(1)

    console.print(f"[cyan]{manifest.package_id}[/cyan] — {len(manifest.files)} files")
    for f in manifest.files:
        console.print(f"  {f}")


@main.command()
@click.argument("expr")
@click.pass_context
def search(ctx: click.Context, expr: str) -> None:
    """Search EXPR across all package manifests."""
    config = ctx.obj["config"]
    results = Manifest.search(expr, Path(config["manifest_dir"]))
    if not results:
        console.print(f"[yellow]No packages contain {expr!r}.[/yellow]")
        return
    for manifest, matches in results:
        console.print(f"\n[cyan]{manifest.package_id}[/cyan] ({manifest.created_at})")
        for f in matches:
            console.print(f"  {f}")


@main.command()
@click.argument("package_id")
@click.option("--dest", "-d", type=click.Path(path_type=Path), default=".", help="Extraction directory")
@click.option("--backend", "-b", help="Backend name prefix to prefer (e.g. 's3://', 'gs://')")
@click.pass_context
def recover(ctx: click.Context, package_id: str, dest: Path, backend: str | None) -> None:
    """Download and extract PACKAGE_ID from the nearest available backend."""
    config = ctx.obj["config"]
    staging = Path(config["staging_dir"])
    staging.mkdir(exist_ok=True)

    try:
        manifest = Manifest.load(package_id, Path(config["manifest_dir"]))
    except FileNotFoundError:
        console.print(f"[red]Package {package_id!r} not found in local manifests.[/red]")
        raise SystemExit(1)

    backends = build_backends(config)
    if not backends:
        console.print("[red]No storage backends configured.[/red]")
        raise SystemExit(1)

    chosen = [b for b in backends if not backend or b.name.startswith(backend)]
    if not chosen:
        console.print(f"[red]No backend matching {backend!r}.[/red]")
        raise SystemExit(1)

    alg = Algorithm(manifest.algorithm)
    key = f"{package_id}.tar.{alg.value}"

    console.print(f"[cyan]Racing {len(chosen)} backend(s) for fastest download...[/cyan]")
    stream = _race_download_stream(chosen, key)
    if stream is None:
        console.print("[red]Download failed from all backends.[/red]")
        raise SystemExit(1)

    console.print(f"[cyan]Extracting to {dest}...[/cyan]")
    try:
        extract_from_stream(stream, dest, alg)
    finally:
        try:
            stream.close()
        except Exception:
            pass

    if manifest.checksum:
        console.print(f"[dim]Checksum verified (sha256:{manifest.checksum[:12]}…)[/dim]")

    console.print(f"[green]Done.[/green] {len(manifest.files)} files extracted to {dest}")


@main.command()
@click.option("--host", default="0.0.0.0", show_default=True)
@click.option("--port", default=8000, show_default=True, type=int)
@click.option("--reload", is_flag=True, default=False, help="Enable auto-reload (dev mode)")
def serve(host: str, port: int, reload: bool) -> None:
    """Start the REST API server."""
    import uvicorn

    uvicorn.run("joganacaixa.api:app", host=host, port=port, reload=reload)
