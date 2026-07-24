# VetAssist AI — versão NUVEM (celular)

> Esta pasta (`24_Consultorio`) é a versão para **uso no celular**,
> hospedada na nuvem. A versão para notebook (com `.exe`/ngrok) fica em
> `23_CONSULTORIO`.

Consultório veterinário virtual: o veterinário atende o tutor por vídeo
em tempo real, enquanto uma IA transcreve a conversa, sugere pontos de
atenção e monta o rascunho do prontuário. Veterinário e tutor usam pelo
navegador do celular, de qualquer lugar.

O que muda em relação à versão notebook:

- **Telas responsivas** (funcionam bem no celular).
- **Login por senha** para o veterinário (`VET_SENHA`) — obrigatório num
  endereço público. O tutor continua entrando só pelo link.
- **Pronta para deploy**: `Dockerfile` na raiz, porta via `PORT`,
  prontuários em `DATA_DIR` (disco persistente).
- Sem ngrok e sem `.exe` — o `https` vem do próprio provedor de nuvem.

**Como publicar: veja `DEPLOY_NUVEM.md`** (passo a passo + provedor
recomendado + nota de LGPD).

## Estrutura

```
23_CONSULTORIO/
├── BACKEND/
│   ├── app/main.py          # API FastAPI (toda a lógica do servidor)
│   ├── requirements.txt
│   ├── run.py                # ponto de entrada (uvicorn, porta 8000)
│   ├── .env                  # chaves e dados do veterinário (NÃO versionar)
│   ├── .env.example          # modelo do .env
│   ├── prontuarios/          # prontuários salvos (NÃO versionar)
│   ├── iniciar_backend.bat   # sobe só o backend
│   └── iniciar_ngrok.bat     # sobe só o túnel
├── frontend/
│   ├── index.html / script.js   # tela do veterinário
│   └── tutor.html / tutor.js    # tela do tutor (abre pelo link recebido)
├── instalar.bat              # instalação (via Python) em notebook novo (1x)
├── iniciar_consultorio.bat   # uso diário (via Python): sobe tudo, 1 clique
├── iniciar_consultorio_exe.bat  # uso diário (via .exe): sobe tudo, 1 clique
├── Abrir Consultorio.url     # atalho para a tela do veterinário
├── MANUAL_DO_USUARIO.docx    # manual não-técnico para o veterinário
├── .gitignore
└── README.md

Arquivos de empacotamento (dentro de BACKEND/):
  app_launcher.py    # ponto de entrada do .exe (sobe servidor + abre tela)
  vetassist.spec     # especificação do PyInstaller
  build_exe.bat      # gera o dist/VetAssist.exe (rodar 1x no Windows)
```

## Como funciona (arquitetura)

- **Vídeo**: WebRTC ponto a ponto entre os navegadores do veterinário e
  do tutor. O backend só faz a sinalização (WebSocket `/ws/sala/{id}`).
  Sem servidor TURN — em redes muito restritivas a conexão pode falhar.
- **Acesso externo**: o backend roda no notebook do veterinário; o
  **ngrok** cria um endereço `https://` público que serve tanto a tela
  do veterinário quanto o link do tutor (obrigatório para
  câmera/microfone fora de localhost).
- **Transcrição**: o navegador grava a chamada em trechos de ~10s e
  envia ao backend (`/ws/transcricao/{id}`). Configurável no `.env`:
  - `TRANSCRICAO=nuvem` — API da OpenAI (whisper-1): rápida e precisa,
    custo por minuto baixo, áudio vai à OpenAI. **Recomendado.**
  - `TRANSCRICAO=local` — faster-whisper na máquina: grátis e privado,
    mas lento em CPU (modelo em `WHISPER_MODEL`).
- **Sugestões clínicas**: `POST /consulta/{id}/sugestoes` manda a
  transcrição pro modelo `OPENAI_MODEL` e devolve alertas + sugestões.
- **Prontuário**: `POST /consulta/{id}/prontuario` gera rascunho
  estruturado (Queixa / Histórico / Sinais / Hipóteses / **Tratamento a
  Seguir** / Orientações), incorporando as anotações digitadas pelo
  veterinário durante a consulta (elas têm prioridade sobre a
  transcrição, ex. doses). `PUT` salva a versão revisada em
  `BACKEND/prontuarios/` com cabeçalho identificado (tutor, animal,
  veterinário, CRMV, contato).
- **Histórico**: `GET /prontuarios` lista tudo; a tela do veterinário
  tem card "Consultas Anteriores" com busca e envio por WhatsApp.
- **Personalização**: nome/CRMV/telefone do veterinário no `.env`
  (`NOME_CLINICA`, `NOME_VETERINARIO`, `CRMV`, `TELEFONE`) — aparecem
  nas duas telas e no prontuário. Dados de cada consulta (tutor/animal)
  são preenchidos na tela antes de iniciar.

## Rodando (desenvolvimento)

```bash
cd BACKEND
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python run.py            # http://127.0.0.1:8000 (escuta em 0.0.0.0)
```

Frontend é servido pelo próprio backend em `/app/index.html`.
Testes rápidos no mesmo PC: abrir `http://127.0.0.1:8000/app/index.html`
em duas abas (vet + tutor). Uso real: subir o ngrok e usar o endereço
`https://...` (ver `iniciar_consultorio.bat`).

## Instalação em outro notebook

Há dois caminhos (o **MANUAL_DO_USUARIO.docx** cobre os dois passo a
passo). Em ambos é preciso instalar o **ngrok** (conta própria + authtoken
em `C:\Ngrok`) e preencher `BACKEND\.env` (chave OpenAI + dados do
veterinário).

**Caminho A — via Python (mais leve de distribuir, atualiza fácil):**
instalar Python com "Add to PATH", copiar a pasta, rodar `instalar.bat`
uma vez, e usar `iniciar_consultorio.bat` no dia a dia.

**Caminho B — via executável (não precisa instalar Python):**
1. Na máquina de desenvolvimento, gere o `.exe`: `cd BACKEND` e rode
   `build_exe.bat`. Sai o `BACKEND\dist\VetAssist.exe` (~30-50 MB, pois a
   transcrição no .exe é na nuvem — `TRANSCRICAO=nuvem`).
2. Copie para o outro notebook: o `VetAssist.exe`, a pasta `frontend`
   **não** é necessária (vai embutida), um arquivo `.env` preenchido ao
   lado do `.exe`, e o `iniciar_consultorio_exe.bat`.
3. No dia a dia: `iniciar_consultorio_exe.bat` (sobe o .exe + ngrok e abre
   a tela). Ou, só para testes no mesmo PC, duplo clique no `VetAssist.exe`
   (abre em `127.0.0.1`, sem ngrok).

O `.exe` só pode ser gerado numa máquina **Windows** (o PyInstaller não
compila para outro sistema operacional).

## Segurança e responsabilidade

- `.env` (chaves) e `prontuarios/` (dados de pacientes) estão no
  `.gitignore` — nunca versionar nem embutir a chave em executável.
- A IA não diagnostica nem prescreve: sugestões e rascunhos sempre saem
  com aviso, e a seção de tratamento registra apenas o que o próprio
  veterinário indicou. A decisão e a assinatura são do veterinário.

## Roadmap

- **v0.6 — Empacotamento (feito)**: dois caminhos prontos — `instalar.bat`
  (via Python) e `build_exe.bat` → `VetAssist.exe` (via PyInstaller, sem
  Python no destino). Falta: gerar o `.exe` numa máquina Windows e testar
  no notebook de destino.
- **v0.7 — Versão móvel para o veterinário** (atendimentos na rua):
  requer decidir onde o backend roda (nuvem, ou notebook em casa via
  túnel).
- **Depois**: domínio fixo/plano pago do ngrok (remove a página de aviso
  e o endereço variável), servidor TURN para redes restritivas,
  histórico por paciente, autenticação, fotos/vídeos enviados pelo
  tutor.
