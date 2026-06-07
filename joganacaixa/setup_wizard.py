"""Interactive setup wizard: installs backend prerequisites, writes the
config file to ~/.joganacaixa.yaml and prints step-by-step credential
instructions for the chosen backend.

The goal is to remove friction: a user runs `joganacaixa setup` and ends
up with a working configuration and a clear checklist of what to do next.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

console = Console()

DEFAULT_CONFIG_PATH = Path("~/.joganacaixa.yaml").expanduser()

# pip packages required per backend type
_BACKEND_PACKAGES = {
    "s3": ["boto3"],
    "gcs": ["google-cloud-storage"],
    "azure": ["azure-storage-blob"],
    "local": [],
}

_STORAGE_CLASSES = {
    "s3": ["standard", "glacier", "deep_archive", "glacier_ir"],
    "gcs": ["standard", "nearline", "coldline", "archive"],
}


def _pip_install(packages: list[str]) -> bool:
    """Install pip packages; return True on success."""
    if not packages:
        return True
    console.print(f"[cyan]Installing: {', '.join(packages)}…[/cyan]")
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", *packages],
            check=True,
        )
        console.print(f"[green]✓ Installed {', '.join(packages)}[/green]")
        return True
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]✗ Installation failed: {exc}[/red]")
        return False


# ---------------------------------------------------------------------------
# Per-backend interactive builders
# ---------------------------------------------------------------------------

def _build_local() -> dict:
    root = Prompt.ask("  Diretório de armazenamento", default="~/joganacaixa-backups")
    expanded = Path(root).expanduser()
    expanded.mkdir(parents=True, exist_ok=True)
    console.print(f"  [green]✓[/green] Diretório criado: {expanded}")
    return {"type": "local", "root": str(expanded)}


def _build_s3() -> dict:
    bucket = Prompt.ask("  Nome do bucket S3")
    region = Prompt.ask("  Região AWS", default="sa-east-1")
    storage_class = Prompt.ask(
        "  Classe de armazenamento",
        choices=_STORAGE_CLASSES["s3"],
        default="standard",
    )
    prefix = Prompt.ask("  Prefixo (pasta dentro do bucket, opcional)", default="backups/")
    return {
        "type": "s3",
        "bucket": bucket,
        "region": region,
        "storage_class": storage_class,
        "prefix": prefix,
    }


def _build_gcs() -> dict:
    bucket = Prompt.ask("  Nome do bucket GCS")
    region = Prompt.ask("  Região", default="southamerica-east1")
    storage_class = Prompt.ask(
        "  Classe de armazenamento",
        choices=_STORAGE_CLASSES["gcs"],
        default="standard",
    )
    prefix = Prompt.ask("  Prefixo (opcional)", default="backups/")
    return {
        "type": "gcs",
        "bucket": bucket,
        "region": region,
        "storage_class": storage_class,
        "prefix": prefix,
    }


def _build_azure() -> dict:
    container = Prompt.ask("  Nome do container", default="backups")
    console.print(
        "  [dim]Dica: deixe em branco para usar a variável de ambiente "
        "AZURE_STORAGE_CONNECTION_STRING[/dim]"
    )
    conn = Prompt.ask("  Connection string", default="${AZURE_STORAGE_CONNECTION_STRING}")
    prefix = Prompt.ask("  Prefixo (opcional)", default="backups/")
    return {
        "type": "azure",
        "container": container,
        "connection_string": conn,
        "prefix": prefix,
    }


_BUILDERS = {
    "local": _build_local,
    "s3": _build_s3,
    "gcs": _build_gcs,
    "azure": _build_azure,
}


# ---------------------------------------------------------------------------
# Credential instructions per backend
# ---------------------------------------------------------------------------

def _instructions(kind: str, entry: dict) -> str:
    if kind == "local":
        return (
            "Nenhuma credencial necessária. O backend local já está pronto para uso.\n"
            f"Os backups serão gravados em: {entry['root']}"
        )
    if kind == "s3":
        return (
            "Configure suas credenciais AWS de uma destas formas:\n\n"
            "[bold]Opção 1 — AWS CLI (recomendado):[/bold]\n"
            "  pip install awscli\n"
            "  aws configure\n"
            f"  (informe a região: {entry['region']})\n\n"
            "[bold]Opção 2 — variáveis de ambiente:[/bold]\n"
            "  export AWS_ACCESS_KEY_ID=AKIA...\n"
            "  export AWS_SECRET_ACCESS_KEY=...\n"
            f"  export AWS_DEFAULT_REGION={entry['region']}\n\n"
            "[bold]Permissões IAM mínimas:[/bold]\n"
            "  s3:PutObject, s3:GetObject, s3:DeleteObject,\n"
            "  s3:ListBucket, s3:CreateBucket, s3:HeadBucket"
        )
    if kind == "gcs":
        return (
            "Configure uma service account do Google Cloud:\n\n"
            "1. Acesse: https://console.cloud.google.com/iam-admin/serviceaccounts\n"
            "2. Crie uma conta de serviço com o papel [bold]Storage Admin[/bold]\n"
            "3. Em Chaves → Adicionar chave → JSON, baixe o arquivo\n"
            "4. Aponte a variável de ambiente para ele:\n"
            "   export GOOGLE_APPLICATION_CREDENTIALS=~/.gcs-key.json\n"
            "   (adicione ao ~/.bashrc ou ~/.zshrc para persistir)"
        )
    if kind == "azure":
        extra = ""
        if "${" in entry.get("connection_string", ""):
            extra = (
                "\n\nVocê escolheu usar variável de ambiente. Defina-a:\n"
                "  export AZURE_STORAGE_CONNECTION_STRING=\"DefaultEndpointsProtocol=https;...\""
            )
        return (
            "Obtenha a connection string do Azure:\n\n"
            "1. Acesse https://portal.azure.com\n"
            "2. Storage accounts → sua conta → Access keys\n"
            "3. Copie a [bold]Connection string[/bold]"
            + extra
        )
    return ""


# ---------------------------------------------------------------------------
# Main wizard
# ---------------------------------------------------------------------------

def run_setup(
    backend_types: list[str] | None = None,
    config_path: Path = DEFAULT_CONFIG_PATH,
    install: bool = True,
    non_interactive: bool = False,
) -> Path:
    """Run the setup wizard. Returns the path of the written config file."""
    console.print(Panel.fit(
        "[bold]Assistente de Configuração — Joga na Caixa[/bold]\n"
        "Vamos configurar seus backends de armazenamento em poucos passos.",
        border_style="cyan",
    ))

    # 1. Choose backends
    if backend_types is None:
        console.print("\n[bold]Passo 1 — Escolha os backends de armazenamento[/bold]")
        console.print("  [dim]Opções: local, s3, gcs, azure[/dim]")
        raw = Prompt.ask(
            "  Backends (separados por vírgula)",
            default="local",
        )
        backend_types = [b.strip() for b in raw.split(",") if b.strip()]

    valid = set(_BUILDERS)
    backend_types = [b for b in backend_types if b in valid]
    if not backend_types:
        console.print("[red]Nenhum backend válido selecionado.[/red]")
        raise SystemExit(1)

    # 2. Install prerequisites
    if install:
        console.print("\n[bold]Passo 2 — Instalando pré-requisitos[/bold]")
        pkgs: list[str] = []
        for b in backend_types:
            pkgs.extend(_BACKEND_PACKAGES.get(b, []))
        pkgs = sorted(set(pkgs))
        if pkgs:
            _pip_install(pkgs)
        else:
            console.print("  [dim]Nenhuma dependência adicional necessária.[/dim]")

    # 3. Collect backend details
    console.print("\n[bold]Passo 3 — Detalhes dos backends[/bold]")
    storage_entries = []
    for b in backend_types:
        console.print(f"\n[cyan]Configurando: {b}[/cyan]")
        if non_interactive:
            # Minimal placeholder entries for non-interactive mode
            storage_entries.append(_placeholder(b))
        else:
            storage_entries.append(_BUILDERS[b]())

    # 4. Encryption choice
    encrypt = True
    if not non_interactive:
        console.print("\n[bold]Passo 4 — Criptografia[/bold]")
        encrypt = Confirm.ask(
            "  Criptografar os backups com AES-256?", default=True
        )

    config = {
        "compression": {
            "algorithm": "zst",
            "level": 3,
            "exclude": [".git", ".escorregador", ".etiqueta", "__pycache__", "*.pyc", "node_modules"],
        },
        "storage": storage_entries,
        "encryption": {
            "enabled": encrypt,
            "key_file": "~/.joganacaixa.key",
        },
        "retries": 3,
        "staging_dir": ".escorregador",
        "manifest_dir": ".etiqueta",
    }

    # 5. Write config
    config_path = config_path.expanduser()
    if config_path.exists() and not non_interactive:
        if not Confirm.ask(
            f"\n[yellow]{config_path} já existe. Sobrescrever?[/yellow]",
            default=False,
        ):
            backup = config_path.with_suffix(".yaml.bak")
            config_path.rename(backup)
            console.print(f"  [dim]Backup salvo em {backup}[/dim]")

    config_path.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True))
    console.print(f"\n[green]✓ Configuração salva em {config_path}[/green]")

    # 6. Print credential instructions
    console.print("\n[bold]Passo 5 — Próximos passos (credenciais)[/bold]")
    for entry in storage_entries:
        kind = entry["type"]
        console.print(Panel(
            _instructions(kind, entry),
            title=f"[bold]{kind.upper()}[/bold]",
            border_style="yellow" if kind != "local" else "green",
        ))

    # 7. Final verification hint
    console.print(Panel.fit(
        "[bold green]Pronto![/bold green]\n\n"
        "Verifique a configuração com:\n"
        "  [cyan]joganacaixa diagnose[/cyan]\n\n"
        "Faça seu primeiro backup com:\n"
        "  [cyan]joganacaixa store /caminho/para/pasta[/cyan]\n\n"
        "Ou inicie a interface web com:\n"
        "  [cyan]joganacaixa serve[/cyan]  →  http://localhost:8000/ui",
        border_style="green",
    ))

    return config_path


def _placeholder(kind: str) -> dict:
    """Minimal placeholder entry used in non-interactive mode."""
    if kind == "local":
        root = str(Path("~/joganacaixa-backups").expanduser())
        Path(root).mkdir(parents=True, exist_ok=True)
        return {"type": "local", "root": root}
    if kind == "s3":
        return {"type": "s3", "bucket": "CHANGE_ME", "region": "sa-east-1",
                "storage_class": "standard", "prefix": "backups/"}
    if kind == "gcs":
        return {"type": "gcs", "bucket": "CHANGE_ME", "region": "southamerica-east1",
                "storage_class": "standard", "prefix": "backups/"}
    if kind == "azure":
        return {"type": "azure", "container": "backups",
                "connection_string": "${AZURE_STORAGE_CONNECTION_STRING}", "prefix": "backups/"}
    return {"type": kind}
