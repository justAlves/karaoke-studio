# Karaoke Studio

Busque músicas no YouTube pelo título ou URL. A lista mostra até cinco resultados com uma prévia de áudio de até 25 segundos, tocada na própria página. Após confirmar uma música, o servidor baixa seu áudio e processa uma música por vez. O processamento separa instrumental, voz principal e vozes de apoio, estima a tonalidade e mistura instrumental com vozes de apoio para o karaokê. Depois busca a letra na [LRCLIB](https://lrclib.net/docs) e a capa do disco na [API de busca do iTunes](https://developer.apple.com/library/archive/documentation/AudioVideo/Conceptual/iTuneSearchAPI/Searching.html).

Na fila, **Iniciar karaokê** abre a música selecionada. **Tocar toda a fila** reproduz em sequência todas as músicas prontas. A tela de karaokê mostra a capa desfocada ao fundo e destaca a linha da letra conforme o áudio avança. Se a LRCLIB não tiver letra sincronizada para a versão escolhida, a música ainda pode ser reproduzida com letra simples ou sem letra. Letra obtida de outra versão pode ter pequenas diferenças de tempo, indicadas na tela.

## Executar

Requer Python 3.10 ou superior, FFmpeg instalado no sistema e acesso à internet.

```bash
cd karaoke-studio
python3 -m venv .venv
.venv/bin/python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py
```

Abra <http://127.0.0.1:8010> no navegador. O projeto usa `yt-dlp` para consultar o YouTube, sem chave de API. Se o YouTube alterar a busca, pode ser necessário atualizar a dependência.

## Deploy com Docker

A imagem padrão usa CPU e funciona em uma VPS comum. Os volumes mantêm a fila, downloads, modelos e faixas mesmo quando o container é recriado:

```bash
docker compose up -d --build
```

Depois abra `http://IP_DO_SERVIDOR:8010`. Para o QR Code gerar o endereço correto do servidor, defina o host público antes de iniciar:

```bash
KARAOKE_HOST=karaoke.exemplo.com docker compose up -d --build
```

Para proteger a sessão com senha no Coolify, crie as variáveis `KARAOKE_PASSWORD` e `KARAOKE_AUTH_SECRET` no painel. A senha é solicitada na primeira abertura e a autenticação fica em um cookie HttpOnly. Em produção atrás de HTTPS, mantenha `KARAOKE_COOKIE_SECURE=true`.

No Coolify, defina também `KARAOKE_PUBLIC_URL` com a URL completa da aplicação, por exemplo `https://karaoke.exemplo.com`. Essa URL será usada no QR Code e no convite da sessão. Use `KARAOKE_COOKIE_SECURE=true` somente quando o acesso estiver em HTTPS; para acesso direto por IP e HTTP, deixe `false`.

### R2 e Firestore

O R2 é usado para armazenar os áudios e stems; o Firestore espelha os metadados da fila. No Coolify, adicione `R2_ENDPOINT_URL`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME` e `FIREBASE_SERVICE_ACCOUNT_B64`. Para gerar a última variável:

```bash
base64 -w0 firebase-service-account.json
```

Cole o resultado como valor da variável no Coolify. As integrações são opcionais: sem essas variáveis, o sistema continua usando os diretórios locais.

Defina `CLOUD_CLEANUP_LOCAL=true` para remover os arquivos locais depois do upload. O karaokê é baixado do R2 novamente quando alguém iniciar a música.

Se usar um IP, informe apenas o host, sem `http://` e sem a porta. Libere a porta 8010 no firewall ou use um proxy reverso HTTPS. Como a sessão não tem autenticação nesta versão, mantenha o serviço atrás de uma rede privada, VPN ou proteção de acesso quando ele estiver em um servidor público.

## Sessão compartilhada

O servidor escuta a rede local e mostra um QR Code na seção da fila. Pessoas conectadas ao mesmo Wi-Fi podem escanear o código, abrir o endereço no celular e adicionar músicas à mesma fila. A porta 8010 precisa estar liberada no firewall. Se o endereço detectado estiver incorreto, inicie com o IP correto:

```bash
KARAOKE_HOST=192.168.0.12 .venv-gpu/bin/python app.py
```

O endereço da sessão é local à rede; não encaminhe a porta do roteador para a internet.

Os modelos de separação são baixados automaticamente na primeira execução, cerca de 300 MB no total. A separação usa os modelos UVR MDX e BVE; a tonalidade é estimada por análise cromática do instrumental. A separação de apoio é aproximada e depende da gravação. A tonalidade pode ser ambígua, especialmente em músicas com modulação ou acordes semelhantes entre maior e menor.

## Aceleração por hardware

O `audio-separator` usa CUDA em GPUs NVIDIA compatíveis, MPS em Macs compatíveis ou CPU. A instalação acima usa CPU. Para criar um ambiente NVIDIA CUDA, instale o driver compatível e use:

```bash
python3 -m venv .venv-gpu
.venv-gpu/bin/python -m pip install torch torchvision
.venv-gpu/bin/python -m pip install -r requirements-gpu.txt
.venv-gpu/bin/python -c 'import torch, onnxruntime as ort; print(torch.cuda.is_available(), ort.get_available_providers())'
.venv-gpu/bin/python app.py
```

Se estiver convertendo um ambiente criado com os comandos de CPU, remova antes `torch`, `torchvision` e `onnxruntime` com `pip uninstall`. Neste computador, o ambiente `.venv-gpu` foi validado com a RTX 4060. Consulte as [instruções do PyTorch](https://pytorch.org/get-started/locally/) e do [audio-separator](https://github.com/nomadkaraoke/python-audio-separator) para selecionar as versões adequadas ao driver. A detecção da tonalidade continua na CPU; a separação, que é a parte mais pesada, pode usar a GPU.

Os arquivos originais ficam em `downloads/`, as faixas e a mistura de karaokê em `stems/`, os modelos em `models/` e a fila com os dados de letra e capa em `data/queue.json`. A fila e o processamento incompleto são retomados quando o servidor reinicia. Esses diretórios não são enviados ao Git.
