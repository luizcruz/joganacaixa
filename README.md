# Joga na Caixa 📦

Ferramenta de backup multi-cloud com compressão plugável, redundância automática e interface web. Envia arquivos comprimidos para múltiplos backends em paralelo (S3, GCS, Azure, local) e mantém um índice local de conteúdo. Inclui detecção de rostos em imagens e sistema de uploads/downloads resumíveis com pause/resume.

---

## Índice

- [Instalação](#instalação)
- [Configuração](#configuração)
- [Linha de Comando (CLI)](#linha-de-comando-cli)
- [Interface Web (UI)](#interface-web-ui)
- [API REST](#api-rest)
- [Reconhecimento Facial](#reconhecimento-facial)
- [Operações Resumíveis](#operações-resumíveis)
- [Arquitetura](#arquitetura)
- [Solução de Problemas](#solução-de-problemas)

---

## Instalação

**Pré-requisitos:** Python 3.10+

```bash
# Clonar o repositório
git clone https://github.com/luizcruz/joganacaixa.git
cd joganacaixa

# Instalar (modo editável com dependências de desenvolvimento)
pip install -e ".[dev]"

# Com suporte a reconhecimento facial (requer cmake + compilador C++)
# Ubuntu/Debian:
sudo apt-get install cmake build-essential
# macOS:
brew install cmake

pip install -e ".[dev,faces]"
```

---

## Configuração

### Configuração rápida (recomendado) ⚡

A forma mais simples de começar é o assistente interativo, que instala as
dependências do backend escolhido, grava o `~/.joganacaixa.yaml` e mostra
um passo a passo de como configurar as credenciais:

```bash
joganacaixa setup
```

O assistente vai:
1. Perguntar quais backends você quer (local, s3, gcs, azure)
2. Instalar os pacotes pip necessários para cada um
3. Coletar os detalhes (bucket, região, etc.)
4. Perguntar se quer criptografia
5. Gravar o arquivo de configuração na sua home
6. Imprimir as instruções de credenciais para cada backend

**Modo não-interativo** (gera um config com placeholders para editar depois):

```bash
# Backend local apenas — funciona imediatamente, sem credenciais
joganacaixa setup -b local

# Múltiplos backends de uma vez
joganacaixa setup -b s3 -b gcs

# Pular instalação de dependências / escolher destino
joganacaixa setup -b s3 --no-install -o ~/.joganacaixa.yaml --non-interactive
```

Depois confirme com `joganacaixa diagnose`.

### Configuração manual

Como alternativa, copie o arquivo de exemplo e edite com suas credenciais:

```bash
cp config.example.yaml ~/.joganacaixa.yaml
```

O arquivo de configuração é opcional — sem ele, o tool usa compressão `zst` e nenhum backend de nuvem.

O arquivo é procurado nas seguintes localizações (em ordem):
1. `.joganacaixa.yaml` no diretório atual
2. `.joganacaixa.yml` no diretório atual
3. `~/.joganacaixa.yaml` (configuração global)

### Exemplo de configuração

```yaml
compression:
  algorithm: zst        # gz | bz2 | xz | zst (padrão: zst — mais rápido com melhor razão)
  level: 3              # nível de compressão zstd (1–19)
  exclude:
    - .git
    - __pycache__
    - node_modules
    - "*.pyc"

storage:
  # AWS S3
  - type: s3
    bucket: meu-bucket-backup
    region: sa-east-1
    storage_class: glacier   # standard | glacier | deep_archive
    prefix: backups/

  # Google Cloud Storage
  - type: gcs
    bucket: meu-bucket-backup
    region: southamerica-east1
    storage_class: archive   # standard | nearline | coldline | archive
    prefix: backups/

  # Azure Blob Storage
  - type: azure
    container: backups
    connection_string: "DefaultEndpointsProtocol=https;AccountName=...;AccountKey=..."
    prefix: backups/

  # Filesystem local (útil para testes sem credenciais de nuvem)
  - type: local
    root: /tmp/meu-backup

encryption:
  enabled: true
  key_file: ~/.joganacaixa.key   # gerada automaticamente no primeiro uso
  # Alternativa: derivar chave de uma senha
  # passphrase_env: JOGANACAIXA_PASSPHRASE

retries: 3
staging_dir: .escorregador
manifest_dir: .etiqueta
```

### Configurando credenciais por backend

#### Backend local (sem credenciais — ideal para testes)

Não precisa de nenhuma credencial. Basta apontar para um diretório:

```yaml
storage:
  - type: local
    root: /tmp/meu-backup
```

#### AWS S3

**Opção 1 — AWS CLI (recomendado):**

```bash
pip install awscli
aws configure
# Preencha: AWS Access Key ID, Secret Access Key, região (ex: sa-east-1), formato (json)
```

Isso cria `~/.aws/credentials` e `~/.aws/config`, lidos automaticamente pelo boto3.

**Opção 2 — variáveis de ambiente:**

```bash
export AWS_ACCESS_KEY_ID=AKIA...
export AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=sa-east-1
```

**Opção 3 — perfil nomeado** (útil para múltiplas contas):

```bash
aws configure --profile meu-perfil
export AWS_PROFILE=meu-perfil
```

**Permissões IAM mínimas** necessárias para o bucket:

```json
{
  "Effect": "Allow",
  "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject",
             "s3:ListBucket", "s3:CreateBucket", "s3:HeadBucket"],
  "Resource": ["arn:aws:s3:::meu-bucket", "arn:aws:s3:::meu-bucket/*"]
}
```

**Config yaml:**

```yaml
storage:
  - type: s3
    bucket: meu-bucket-backup
    region: sa-east-1
    storage_class: standard   # standard | glacier | deep_archive
    prefix: backups/          # opcional — pasta dentro do bucket
```

#### Google Cloud Storage (GCS)

**Passo 1 — criar service account:**

1. Acesse [console.cloud.google.com/iam-admin/serviceaccounts](https://console.cloud.google.com/iam-admin/serviceaccounts)
2. Clique em **Criar conta de serviço**
3. Dê um nome (ex: `joganacaixa-backup`)
4. Atribua o papel **Storage Admin** (ou crie um papel customizado com as permissões abaixo)
5. Clique em **Chaves → Adicionar chave → JSON**
6. Salve o arquivo baixado em um local seguro (ex: `~/.gcs-key.json`)

**Permissões mínimas** no papel customizado:
- `storage.buckets.create`
- `storage.buckets.get`
- `storage.objects.create`
- `storage.objects.get`
- `storage.objects.delete`
- `storage.objects.list`

**Passo 2 — apontar a variável:**

```bash
export GOOGLE_APPLICATION_CREDENTIALS=~/.gcs-key.json
# Adicione ao ~/.bashrc ou ~/.zshrc para persistir
```

**Config yaml:**

```yaml
storage:
  - type: gcs
    bucket: meu-bucket-backup
    region: southamerica-east1
    storage_class: standard   # standard | nearline | coldline | archive
    prefix: backups/
```

#### Azure Blob Storage

**Passo 1 — obter a connection string:**

1. Acesse o [portal.azure.com](https://portal.azure.com)
2. Navegue até **Storage accounts → sua conta → Access keys**
3. Copie a **Connection string** (começa com `DefaultEndpointsProtocol=https;...`)

**Config yaml:**

```yaml
storage:
  - type: azure
    container: backups
    connection_string: "DefaultEndpointsProtocol=https;AccountName=minhacontа;AccountKey=CHAVE==;EndpointSuffix=core.windows.net"
    prefix: backups/
```

> ⚠️ A connection string contém credenciais sensíveis. Considere usar uma variável de ambiente:
>
> ```bash
> export AZURE_STORAGE_CONNECTION_STRING="DefaultEndpointsProtocol=https;..."
> ```
>
> E no yaml:
> ```yaml
> connection_string: "${AZURE_STORAGE_CONNECTION_STRING}"
> ```

---

## Linha de Comando (CLI)

### Configuração inicial

```bash
joganacaixa setup              # assistente interativo
joganacaixa setup -b local     # configura só o backend local (sem credenciais)
joganacaixa diagnose           # verifica qual config está carregado e os backends
```

### Armazenar arquivos

```bash
# Comprimir e enviar o diretório atual para todos os backends
joganacaixa store

# Enviar um arquivo ou diretório específico
joganacaixa store /caminho/para/pasta
joganacaixa store /caminho/para/arquivo.zip

# Escolher algoritmo de compressão
joganacaixa store -a gz .    # gzip — suporte universal
joganacaixa store -a bz2 .   # bzip2 — melhor razão que gz
joganacaixa store -a xz .    # xz — melhor razão, mais lento
joganacaixa store -a zst .   # zstandard — rápido + boa razão (padrão)
```

### Listar pacotes

```bash
# Listar todos os pacotes armazenados
joganacaixa list
```

Saída:
```
┌──────────────┬──────────────────────┬───────┬──────────────┬────────────────────────────┐
│ Package ID   │ Created At           │ Algo  │ Files        │ Locations                  │
├──────────────┼──────────────────────┼───────┼──────────────┼────────────────────────────┤
│ 1718000000   │ 2024-06-10T12:00:00Z │ zst   │ 42 files     │ s3://bucket, gs://bucket   │
└──────────────┴──────────────────────┴───────┴──────────────┴────────────────────────────┘
```

### Inspecionar um pacote

```bash
# Ver os arquivos dentro de um pacote
joganacaixa contents 1718000000
```

### Buscar arquivos

```bash
# Encontrar pacotes que contêm um determinado arquivo
joganacaixa search config.yaml
joganacaixa search ".env"
```

### Recuperar (baixar e extrair)

```bash
# Baixar e extrair um pacote
joganacaixa recover 1718000000

# Preferir um backend específico
joganacaixa recover 1718000000 -b s3://
joganacaixa recover 1718000000 -b gs://
joganacaixa recover 1718000000 -b local://
```

> **Nota sobre Glacier/Deep Archive:** recuperações de S3 Glacier precisam ser iniciadas primeiro pelo console AWS ou SDK. Este tool cuida apenas do upload e do download após a restauração.

---

## Interface Web (UI)

### Iniciando o servidor

```bash
# Servidor em http://localhost:8000
joganacaixa serve

# Modo dev com reload automático
joganacaixa serve --reload

# Porta customizada
joganacaixa serve --port 9000
```

Acesse **http://localhost:8000/ui** no navegador.

### Abas da interface

#### Dashboard
Visão geral: total de pacotes, arquivos indexados, backends e algoritmos em uso. Lista os pacotes mais recentes com atalho para detalhes.

#### Store
Arraste um arquivo ou clique para selecionar. Escolha o algoritmo de compressão e clique em enviar. O progresso é exibido em tempo real via SSE e a operação aparece no **painel de Operações Ativas** na parte inferior da tela.

#### Packages
Grade com todos os pacotes armazenados. Cada cartão mostra:
- ID e data de criação
- Algoritmo de compressão
- Número de arquivos
- Backends onde está armazenado

Ações disponíveis por pacote:
- **Arquivos** — expande a lista de arquivos dentro do pacote
- **Recover** — inicia download resumível (acompanhe no painel de Operações)
- **Delete** — remove de todos os backends e apaga o manifest local

#### Search
Busca por nome de arquivo em todos os pacotes. Digitar inicia a busca com debounce de 320 ms. Resultados mostram o pacote e os arquivos encontrados com destaque do termo buscado.

#### Faces
> Requer instalação com `[faces]`: `pip install -e ".[faces]"`

Grade de rostos detectados nas imagens armazenadas. Clicar em um rosto abre um modal com:
- Todas as ocorrências (pacote + caminho da imagem)
- Botão **Baixar todas as imagens** — gera e baixa um ZIP com as imagens onde aquele rosto aparece

Botão **Indexar todos** processa todos os pacotes que contêm imagens e atualiza o índice de rostos.

#### Painel de Operações Ativas (barra inferior)
Aparece automaticamente quando há operações em curso. Para cada operação exibe:
- Tipo (store / recover) e nome do arquivo
- Barra de progresso com percentual
- Status: `Em progresso`, `Pausado`, `Sem conexão — aguardando…`, `Concluído`, `Falhou`
- Botões **Pausar**, **Retomar** e **✕ Cancelar**

**Alertas automáticos (toasts):**
- 🔴 `Operação pausada — sem conexão` quando a rede cai
- 🟢 `Conexão restaurada — retomando` quando a rede volta
- ℹ️ `Operação pausada` / `Operação retomada` ao pausar/retomar manualmente

---

## API REST

A documentação interativa completa está em **http://localhost:8000/docs**.

### Pacotes

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| `GET` | `/packages` | Lista todos os pacotes |
| `GET` | `/packages/{id}` | Metadados e lista de arquivos de um pacote |
| `GET` | `/search?expr=` | Busca arquivos em todos os manifests |
| `POST` | `/store` | Upload síncrono (multipart); `?algorithm=gz\|bz2\|xz\|zst` |
| `GET` | `/recover/{id}` | Download do arquivo; `?backend=s3://` para preferir um backend |
| `DELETE` | `/packages/{id}` | Remove de todos os backends e apaga o manifest |

### Operações Resumíveis

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| `POST` | `/store/resumable` | Inicia upload assíncrono → `{ operation_id }` |
| `POST` | `/recover/{id}/resumable` | Inicia download assíncrono → `{ operation_id }` |
| `GET` | `/operations` | Lista todas as operações ativas e recentes |
| `GET` | `/operations/{id}` | Status de uma operação específica |
| `POST` | `/operations/{id}/pause` | Pausa a operação |
| `POST` | `/operations/{id}/resume` | Retoma a operação |
| `DELETE` | `/operations/{id}` | Cancela a operação |
| `GET` | `/operations/{id}/events` | **SSE** — stream de progresso em tempo real |

**Exemplo de uso da API resumível:**

```bash
# Iniciar upload
curl -X POST http://localhost:8000/store/resumable \
  -F "file=@backup.tar" \
  -F "algorithm=zst"
# → { "operation_id": "a1b2c3d4" }

# Acompanhar progresso via SSE
curl -N http://localhost:8000/operations/a1b2c3d4/events

# Pausar
curl -X POST http://localhost:8000/operations/a1b2c3d4/pause

# Retomar
curl -X POST http://localhost:8000/operations/a1b2c3d4/resume

# Cancelar
curl -X DELETE http://localhost:8000/operations/a1b2c3d4
```

### Rostos

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| `GET` | `/faces` | Lista todos os clusters de rosto |
| `GET` | `/faces/{id}` | Detalhes e ocorrências de um cluster |
| `GET` | `/faces/{id}/thumbnail` | Imagem JPEG do rosto (160×160) |
| `POST` | `/faces/index/{pkg_id}` | Indexa rostos em um pacote; `?force=true` para re-indexar |
| `POST` | `/faces/index` | Indexa todos os pacotes com imagens |
| `GET` | `/faces/{id}/images.zip` | ZIP de todas as imagens onde o rosto aparece |

---

## Reconhecimento Facial

O índice de rostos é mantido em `.etiqueta/faces/`:

```
.etiqueta/
└── faces/
    ├── index.json            # clusters + ocorrências + encodings 128-d
    └── face_thumbnails/
        └── face_<id>.jpg     # thumbnail 160×160 por cluster
```

O algoritmo:
1. Baixa o arquivo do pacote de um backend disponível
2. Extrai apenas os arquivos de imagem (`.jpg`, `.jpeg`, `.png`, `.bmp`, `.tiff`, `.webp`)
3. Detecta rostos com `face_recognition` (HOG model)
4. Compara os vetores de 128 dimensões com clusters existentes (threshold 0.55)
5. Cria novo cluster se nenhum match, ou adiciona ocorrência ao mais próximo
6. Salva thumbnail recortado do rosto
7. Deleta os temporários

Para ajustar a sensibilidade, edite `_MATCH_THRESHOLD` em `joganacaixa/faces.py`:
- Valor menor → mais rigoroso (rostos muito semelhantes viram clusters distintos)
- Valor maior → mais permissivo (rostos parecidos agrupados juntos)

---

## Operações Resumíveis

Uploads e downloads longos são executados em threads de background com suporte a:

- **Pause manual** via UI ou `POST /operations/{id}/pause`
- **Resume manual** via UI ou `POST /operations/{id}/resume`
- **Auto-pause** ao detectar falha de rede → status `no_connection`
- **Auto-resume** quando a conexão é restaurada (monitor verifica a cada 15 s)
- **Retry com backoff exponencial** (2s, 4s, 8s, ... até 60s) por backend
- **Progresso em tempo real** via SSE em chunks de 512 KB

---

## Arquitetura

```
joganacaixa/
├── compression.py   # tar + gz/bz2/xz/zst; Algorithm enum
├── manifest.py      # manifests JSON em .etiqueta/; busca e listagem
├── config.py        # carrega YAML; factory build_backends()
├── cli.py           # CLI Click: store, list, contents, search, recover, serve
├── api.py           # FastAPI: endpoints de pacotes, operações, faces
├── faces.py         # detecção, encoding, clustering e índice de rostos
├── operations.py    # máquina de estados de operações; SSE fan-out
├── resumable.py     # upload/download em chunks com pause/cancel/retry
├── reliability.py   # retry com backoff exponencial
├── setup_wizard.py  # assistente de configuração interativo (joganacaixa setup)
├── encryption.py    # AES-256 encrypt/decrypt de arquivos
└── storage/
    ├── base.py      # StorageBackend ABC
    ├── local.py     # LocalBackend — filesystem
    ├── s3.py        # S3Backend — AWS S3 + Glacier
    ├── gcs.py       # GCSBackend — Google Cloud Storage
    └── azure.py     # AzureBackend — Azure Blob Storage

frontend/
└── index.html       # SPA: Dashboard, Store, Packages, Search, Faces, Operações

tests/
├── test_compression.py
├── test_encryption.py
├── test_manifest.py
├── test_storage.py
├── test_faces.py
└── test_operations.py
```

**Fluxo store:** arquivo → `compress()` → `.escorregador/<ts>.tar.<alg>` → upload paralelo para todos os backends (com pause/resume) → `build_manifest()` → `.etiqueta/<ts>.json` → arquivo de staging deletado.

**Fluxo recover:** `.etiqueta/<id>.json` → escolhe backend → download para `.escorregador/` (com resume por byte offset) → extrai → staging deletado.

**Fluxo face indexing:** trigger via API → baixa archive → extrai imagens em temp dir → detecta rostos → compara encodings → cria/atualiza clusters → salva thumbnails → atualiza `.etiqueta/faces/index.json` → limpa temp.

---

## Executando os Testes

```bash
pytest                          # todos os testes
pytest tests/test_compression.py -v
pytest tests/test_operations.py -v
pytest tests/test_faces.py -v
```

---

---

## Solução de Problemas

### "No storage backends configured" na interface web

**Causa mais comum:** o arquivo de configuração carregado pelo servidor não é o seu `~/.joganacaixa.yaml`.

**Diagnóstico rápido:**

```bash
joganacaixa diagnose
```

Saída esperada:
```
Config file: /home/seu-usuario/.joganacaixa.yaml
Compression: zst
Backends (2):
  ✓ s3   meu-bucket
  ✓ local /tmp/backup
```

Se aparecer `none found — using built-in defaults`, nenhum arquivo de config foi encontrado.

**Por que o `~/.joganacaixa.yaml` pode ser ignorado:**

O arquivo é procurado nesta ordem de prioridade:
1. `.joganacaixa.yaml` no **diretório atual** (CWD)
2. `.joganacaixa.yml` no **diretório atual**
3. `~/.joganacaixa.yaml` (global)

Se você rodar `joganacaixa serve` de dentro de um diretório que já tenha um `.joganacaixa.yaml` (mesmo vazio ou incompleto), ele prevalece sobre o global.

**Soluções:**

```bash
# Opção 1 — passar o caminho explicitamente
joganacaixa --config ~/.joganacaixa.yaml serve

# Opção 2 — usar variável de ambiente
export JOGANACAIXA_CONFIG=~/.joganacaixa.yaml
joganacaixa serve

# Opção 3 — rodar o serve a partir da sua home
cd ~
joganacaixa serve
```

### Verificar sintaxe do YAML

```bash
python3 -c "import yaml; yaml.safe_load(open('~/.joganacaixa.yaml'.replace('~', __import__('os').path.expanduser('~'))))" && echo "OK"
```

### A seção `storage:` existe mas está vazia

Certifique-se de que o YAML tem pelo menos uma entrada de backend **sem comentários**:

```yaml
# ✗ errado — sem entradas reais
storage:
  # - type: s3
  #   bucket: ...

# ✓ correto — pelo menos um backend ativo
storage:
  - type: local
    root: /tmp/meu-backup
```

Para testar sem credenciais de nuvem, use o backend `local`:

```yaml
storage:
  - type: local
    root: /tmp/joganacaixa-test
```

### Credenciais de nuvem

| Backend | O que verificar |
|---------|-----------------|
| S3 | `aws configure list` ou variáveis `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` |
| GCS | `echo $GOOGLE_APPLICATION_CREDENTIALS` aponta para um JSON válido |
| Azure | `connection_string` no yaml está correto |

---

## Adicionando um Novo Backend de Storage

1. Crie `joganacaixa/storage/<nome>.py` implementando `StorageBackend`:
   - `upload(local_path, key) → str`
   - `download(key, local_path) → None`
   - `list_packages() → list[str]`
   - `delete(key) → None`
   - Opcionalmente sobrescreva `upload_stream(data, key)` e `download_stream(key, offset=0)` para eficiência
2. Registre o tipo em `config.py` → `build_backends()`
3. Adicione o SDK ao `pyproject.toml`
