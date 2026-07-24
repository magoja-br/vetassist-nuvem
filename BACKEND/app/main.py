import asyncio
import json
import logging
import os
import socket
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from dotenv import load_dotenv
from fastapi import (
    Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Caminhos base — funcionam tanto rodando como script (python run.py) quanto
# empacotado como .exe (PyInstaller).
#
# _DIR_RECURSOS: onde ficam os arquivos que acompanham o programa (frontend).
#   No .exe onefile, o PyInstaller extrai isso numa pasta temporária
#   (sys._MEIPASS); rodando como script, é a raiz do projeto.
# _DIR_DADOS: onde ficam .env e os prontuários salvos — sempre AO LADO do
#   executável/projeto, pra persistir e o veterinário poder editar o .env.
# ---------------------------------------------------------------------------
_EMPACOTADO = getattr(sys, "frozen", False)

if _EMPACOTADO:
    _DIR_RECURSOS = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    _DIR_DADOS = Path(sys.executable).resolve().parent
else:
    _DIR_RECURSOS = Path(__file__).resolve().parent.parent.parent
    _DIR_DADOS = Path(__file__).resolve().parent.parent  # pasta BACKEND

# Lê o .env (chaves de API, dados do veterinário, config do modelo) que fica
# ao lado do programa. Obs: mudanças no .env exigem reiniciar o servidor.
load_dotenv(_DIR_DADOS / ".env")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vetassist")

APP_VERSION = "0.6.0"

app = FastAPI(
    title="VetAssist AI",
    version=APP_VERSION
)

# Em produção, restrinja allow_origins ao domínio real do frontend.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Serve o frontend em /app (ex: http://<ip-da-maquina>:8000/app/index.html).
#
# Servir pelo mesmo processo/porta do backend permite abrir a tela em
# outro dispositivo na rede (celular do tutor) sem precisar configurar
# outro servidor — e o frontend (script.js/tutor.js) descobre sozinho o
# endereço certo do backend via window.location, então funciona tanto em
# 127.0.0.1 quanto no IP da rede local.
# ---------------------------------------------------------------------------

_DIR_FRONTEND = _DIR_RECURSOS / "frontend"


class StaticSemCache(StaticFiles):
    """
    Serve o frontend proibindo cache no navegador. Sem isso, o Chrome
    guarda o script.js antigo e o usuário continua rodando código velho
    mesmo depois de atualizarmos os arquivos — difícil de diagnosticar.
    O custo é desprezível (arquivos pequenos, rede local).
    """

    async def get_response(self, path, scope):
        resposta = await super().get_response(path, scope)
        resposta.headers["Cache-Control"] = "no-store, must-revalidate"
        return resposta


if _DIR_FRONTEND.is_dir():
    app.mount(
        "/app", StaticSemCache(directory=str(_DIR_FRONTEND), html=True), name="frontend"
    )
else:
    logger.warning("pasta do frontend não encontrada em %s", _DIR_FRONTEND)


class IniciarConsultaRequest(BaseModel):
    nome_tutor: str = ""
    nome_animal: str = ""


class ConsultaResponse(BaseModel):
    consulta_id: str
    status: str
    iniciada_em: str


class ConsultaInfoResponse(BaseModel):
    consulta_id: str
    nome_tutor: str
    nome_animal: str
    nome_veterinario: str
    nome_clinica: str


# consulta_id -> {"nome_tutor": ..., "nome_animal": ...}
consultas_info: Dict[str, Dict[str, str]] = {}


# Personalização da marca — definida no .env de cada instalação, assim o
# mesmo software serve pra qualquer veterinário sem mudar código.
NOME_CLINICA = os.getenv("NOME_CLINICA", "VetAssist AI")
NOME_VETERINARIO = os.getenv("NOME_VETERINARIO", "")
CRMV = os.getenv("CRMV", "")
TELEFONE = os.getenv("TELEFONE", "")
SUBTITULO_PADRAO = "Assistente Inteligente para Consultas Veterinárias"

# ---------------------------------------------------------------------------
# Servidores ICE (STUN/TURN) para a videochamada.
#
# STUN sozinho não basta quando os dois lados estão em redes diferentes
# (ex.: dois celulares no 4G) — aí precisa de um TURN, que faz a ponte.
# Por padrão usamos um TURN público gratuito (Open Relay) para funcionar
# de imediato. Para mais estabilidade, crie uma conta grátis (ex.:
# metered.ca, 50GB/mês) e informe TURN_URL / TURN_USER / TURN_PASS nas
# variáveis de ambiente — elas entram automaticamente.
# ---------------------------------------------------------------------------
def _montar_ice_servers():
    servers = [
        {"urls": "stun:stun.l.google.com:19302"},
        # TURN público gratuito (Open Relay) — bom para testes.
        {
            "urls": "turn:openrelay.metered.ca:80",
            "username": "openrelayproject",
            "credential": "openrelayproject",
        },
        {
            "urls": "turn:openrelay.metered.ca:443",
            "username": "openrelayproject",
            "credential": "openrelayproject",
        },
        {
            "urls": "turn:openrelay.metered.ca:443?transport=tcp",
            "username": "openrelayproject",
            "credential": "openrelayproject",
        },
    ]
    # TURN próprio (opcional), via variáveis de ambiente:
    turn_url = os.getenv("TURN_URL", "").strip()
    if turn_url:
        servers.append({
            "urls": turn_url,
            "username": os.getenv("TURN_USER", ""),
            "credential": os.getenv("TURN_PASS", ""),
        })
    return servers


ICE_SERVERS = _montar_ice_servers()

# ---------------------------------------------------------------------------
# Senha do veterinário (necessária quando hospedado na nuvem, endereço
# público). Se VET_SENHA estiver vazia (uso local no notebook), o acesso
# fica aberto e nada muda. Quando definida, os endpoints que gastam a
# chave da OpenAI ou expõem prontuários exigem a senha.
# ---------------------------------------------------------------------------
VET_SENHA = os.getenv("VET_SENHA", "").strip()


async def exigir_vet(authorization: str | None = Header(default=None)):
    """Dependência: valida a senha do veterinário nos endpoints protegidos."""
    if not VET_SENHA:
        return  # sem senha configurada => acesso aberto (uso local)
    if authorization != f"Bearer {VET_SENHA}":
        raise HTTPException(
            status_code=401,
            detail="Acesso restrito ao veterinário. Faça login novamente.",
        )


class ConfigResponse(BaseModel):
    lan_ip: str
    porta: int
    nome_clinica: str
    nome_veterinario: str
    crmv: str
    telefone: str
    subtitulo: str
    auth_required: bool
    ice_servers: list = []


@app.get("/auth/verificar")
def verificar_senha(_=Depends(exigir_vet)):
    """A tela do vet chama isto após o login pra confirmar a senha."""
    return {"ok": True}


def _obter_ip_local() -> str:
    """
    Descobre o IP da máquina na rede local (ex: 192.168.x.x), sem
    depender de como a página foi aberta (127.0.0.1 ou o próprio IP).

    Truque: abre um socket UDP "conectado" a um endereço externo (não
    envia nada de verdade) só pra perguntar ao sistema operacional qual
    interface de rede seria usada — e pega o IP local dessa interface.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


@app.get("/config", response_model=ConfigResponse)
def obter_config():
    """
    Usado pelo frontend pra montar o link do tutor com o IP certo,
    mesmo que o veterinário tenha aberto a página em 127.0.0.1
    (necessário no navegador do notebook para liberar câmera/microfone).
    """
    if NOME_VETERINARIO:
        partes = [f"Dr. {NOME_VETERINARIO}"]
        if CRMV:
            partes.append(CRMV)
        if TELEFONE:
            partes.append(TELEFONE)
        subtitulo = " • ".join(partes)
    else:
        subtitulo = SUBTITULO_PADRAO

    return ConfigResponse(
        lan_ip=_obter_ip_local(),
        porta=8000,
        nome_clinica=NOME_CLINICA,
        nome_veterinario=NOME_VETERINARIO,
        crmv=CRMV,
        telefone=TELEFONE,
        subtitulo=subtitulo,
        auth_required=bool(VET_SENHA),
        ice_servers=ICE_SERVERS,
    )


@app.get("/")
def read_root():
    return {
        "status": "online",
        "system": "VetAssist AI",
        "version": APP_VERSION
    }


@app.get("/health")
def health():
    """Usado pelo frontend para checar se o backend está no ar."""
    return {"status": "ok"}


@app.post(
    "/consulta/iniciar",
    response_model=ConsultaResponse,
    dependencies=[Depends(exigir_vet)],
)
def iniciar_consulta(corpo: IniciarConsultaRequest | None = None):
    """
    Inicia uma nova sessão de consulta, opcionalmente já identificada
    com o nome do tutor e do animal (preenchidos pelo veterinário).
    """
    consulta_id = str(uuid.uuid4())

    consultas_info[consulta_id] = {
        "nome_tutor": (corpo.nome_tutor.strip() if corpo else ""),
        "nome_animal": (corpo.nome_animal.strip() if corpo else ""),
    }

    return ConsultaResponse(
        consulta_id=consulta_id,
        status="iniciada",
        iniciada_em=datetime.now(timezone.utc).isoformat()
    )


@app.get("/consulta/{consulta_id}/info", response_model=ConsultaInfoResponse)
def obter_consulta_info(consulta_id: str):
    """Usado pela tela do tutor pra saudar pelo nome (João, Rex etc.)."""
    info = consultas_info.get(consulta_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Consulta não encontrada.")

    return ConsultaInfoResponse(
        consulta_id=consulta_id,
        nome_tutor=info.get("nome_tutor", ""),
        nome_animal=info.get("nome_animal", ""),
        nome_veterinario=NOME_VETERINARIO,
        nome_clinica=NOME_CLINICA,
    )


# ---------------------------------------------------------------------------
# Sinalização WebRTC (v0.3)
#
# Não trafega vídeo/áudio pelo backend — apenas as mensagens necessárias
# para vet e tutor negociarem uma conexão P2P direta (offer/answer/ICE).
# ---------------------------------------------------------------------------

PAPEIS_VALIDOS = ("vet", "tutor")

# consulta_id -> {"vet": WebSocket, "tutor": WebSocket}
salas: Dict[str, Dict[str, WebSocket]] = {}


@app.websocket("/ws/sala/{consulta_id}")
async def ws_sala(websocket: WebSocket, consulta_id: str, papel: str):
    if papel not in PAPEIS_VALIDOS:
        await websocket.close(code=4000)
        return

    await websocket.accept()
    sala = salas.setdefault(consulta_id, {})

    if papel in sala:
        await websocket.send_json({
            "tipo": "erro",
            "mensagem": f"já existe um '{papel}' conectado nesta sala"
        })
        await websocket.close(code=4001)
        return

    sala[papel] = websocket
    outro_papel = "tutor" if papel == "vet" else "vet"

    # Se o outro participante já estava na sala, avisa os dois lados
    # para que o veterinário (sempre o ofertante) inicie a negociação.
    peer = sala.get(outro_papel)
    if peer is not None:
        await peer.send_json({"tipo": "peer-entrou"})
        await websocket.send_json({"tipo": "peer-entrou"})

    try:
        while True:
            mensagem = await websocket.receive_json()
            peer = sala.get(outro_papel)
            if peer is not None:
                await peer.send_json(mensagem)
    except WebSocketDisconnect:
        pass
    finally:
        if sala.get(papel) is websocket:
            del sala[papel]

        peer = sala.get(outro_papel)
        if peer is not None:
            try:
                await peer.send_json({"tipo": "peer-saiu"})
            except Exception:
                pass

        if not sala:
            salas.pop(consulta_id, None)


# ---------------------------------------------------------------------------
# Transcrição (v0.4; nuvem adicionada depois)
#
# O navegador do veterinário grava trechos do áudio da chamada (voz local
# + voz remota já mixadas) e envia por WebSocket em binário. O backend
# transcreve de um de dois jeitos, conforme TRANSCRICAO no .env:
#
#   TRANSCRICAO=nuvem  -> API da OpenAI (whisper-1): rápida e precisa,
#                          custo baixo por minuto; o áudio é enviado à
#                          OpenAI. Requer OPENAI_API_KEY.
#   TRANSCRICAO=local  -> faster-whisper na própria máquina: grátis e
#                          privado, mas lento em CPU (padrão).
# ---------------------------------------------------------------------------

WHISPER_MODEL_NAME = os.getenv("WHISPER_MODEL", "medium")
MODO_TRANSCRICAO = os.getenv("TRANSCRICAO", "local").strip().lower()
OPENAI_TRANSCRIBE_MODEL = os.getenv("OPENAI_TRANSCRIBE_MODEL", "whisper-1")
_whisper_model = None  # carregado sob demanda, na primeira transcrição

# consulta_id -> lista de trechos transcritos, em ordem
transcricoes: Dict[str, List[str]] = {}


def _obter_modelo_whisper():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        _whisper_model = WhisperModel(
            WHISPER_MODEL_NAME, device="cpu", compute_type="int8"
        )
    return _whisper_model


def _transcrever_audio(audio_bytes: bytes, contexto: str = "") -> str:
    """Roda em thread separada (é CPU-bound) — ver run_in_executor abaixo."""
    modelo = _obter_modelo_whisper()

    # delete=False + fechar antes de reabrir: no Windows, um arquivo aberto
    # por este processo não pode ser aberto de novo por outra biblioteca
    # (o faster-whisper abre o caminho por conta própria) enquanto o
    # handle original ainda está ativo — dá "Permission denied".
    arquivo_temp = tempfile.NamedTemporaryFile(suffix=".webm", delete=False)
    caminho = arquivo_temp.name
    try:
        arquivo_temp.write(audio_bytes)
        arquivo_temp.close()

        segmentos, _info = modelo.transcribe(
            caminho,
            language="pt",
            vad_filter=True,
            # beam_size=1 (greedy): ~2-3x mais rápido que o padrão (5),
            # essencial pra acompanhar o tempo real em CPU. A perda de
            # qualidade é pequena, e o contexto abaixo compensa parte.
            beam_size=1,
            # Passa o final da transcrição anterior como contexto: como
            # cada trecho chega isolado, sem isso o modelo recomeça "do
            # zero" a cada trecho e o texto sai picotado/incoerente.
            initial_prompt=contexto if contexto else None,
        )
        texto = " ".join(segmento.text.strip() for segmento in segmentos)
    finally:
        try:
            os.remove(caminho)
        except OSError:
            pass

    return texto.strip()


def _transcrever_audio_nuvem(audio_bytes: bytes, contexto: str = "") -> str:
    """Transcreve via API da OpenAI (rápido; requer chave e internet)."""
    from openai import OpenAI

    cliente = OpenAI(api_key=OPENAI_API_KEY)
    resultado = cliente.audio.transcriptions.create(
        model=OPENAI_TRANSCRIBE_MODEL,
        file=("trecho.webm", audio_bytes),
        language="pt",
        # 'prompt' cumpre o mesmo papel do initial_prompt local: dá o
        # final da transcrição anterior como contexto de continuidade.
        prompt=contexto if contexto else None,
    )
    return (resultado.text or "").strip()


def _transcrever(audio_bytes: bytes, contexto: str = "") -> str:
    if MODO_TRANSCRICAO == "nuvem":
        if not OPENAI_API_KEY:
            raise RuntimeError(
                "TRANSCRICAO=nuvem exige OPENAI_API_KEY no .env"
            )
        return _transcrever_audio_nuvem(audio_bytes, contexto)
    return _transcrever_audio(audio_bytes, contexto)


@app.websocket("/ws/transcricao/{consulta_id}")
async def ws_transcricao(websocket: WebSocket, consulta_id: str):
    await websocket.accept()
    loop = asyncio.get_event_loop()

    try:
        while True:
            audio_bytes = await websocket.receive_bytes()
            logger.info(
                "trecho de áudio recebido (%d bytes) — consulta %s",
                len(audio_bytes), consulta_id
            )

            if len(audio_bytes) < 1000:
                # trecho vazio/silencioso demais, não vale a pena transcrever
                logger.info("trecho descartado: menor que 1000 bytes")
                continue

            # Contexto: últimos ~200 caracteres já transcritos desta
            # consulta, pro modelo emendar os trechos com coerência.
            anteriores = transcricoes.get(consulta_id, [])
            contexto = " ".join(anteriores)[-200:] if anteriores else ""

            try:
                texto = await loop.run_in_executor(
                    None, _transcrever, audio_bytes, contexto
                )
            except Exception as erro:
                logger.exception("falha ao transcrever trecho")
                await websocket.send_json({
                    "tipo": "erro",
                    "mensagem": f"falha ao transcrever: {erro}"
                })
                continue

            if texto:
                logger.info("texto transcrito: %s", texto)
                transcricoes.setdefault(consulta_id, []).append(texto)
                await websocket.send_json({"tipo": "transcricao", "texto": texto})
            else:
                logger.info(
                    "trecho transcrito veio vazio (provavelmente silêncio "
                    "detectado pelo VAD — verifique se o áudio está sendo "
                    "capturado corretamente)"
                )
    except WebSocketDisconnect:
        pass


# ---------------------------------------------------------------------------
# Sugestões clínicas via LLM (v0.4)
#
# Usa a transcrição acumulada da consulta para sugerir hipóteses, perguntas
# e sinais de atenção. Requer OPENAI_API_KEY em BACKEND/.env — nunca deve
# ficar embutida no código nem no executável final; ver .env.example.
#
# Importante: a IA nunca decide nem prescreve. Ela só organiza informação
# para o veterinário avaliar — o texto do prompt reforça isso, e a resposta
# sempre inclui um aviso nesse sentido.
# ---------------------------------------------------------------------------

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

AVISO_RESPONSABILIDADE = (
    "Sugestões geradas por IA para apoio ao veterinário. Não são diagnóstico "
    "nem prescrição — a decisão clínica é sempre do veterinário responsável."
)

PROMPT_SISTEMA_SUGESTOES = (
    "Você é um assistente de apoio para um médico veterinário durante uma "
    "consulta por vídeo. Vai receber a transcrição da conversa entre o "
    "veterinário e o tutor do animal. A partir dela, gere:\n"
    "1. 'alertas': sinais de atenção/urgência mencionados que merecem destaque.\n"
    "2. 'sugestoes': possíveis hipóteses e perguntas úteis para o veterinário "
    "considerar a seguir.\n"
    "Nunca dê diagnóstico definitivo nem prescrição — apenas apoio à decisão, "
    "o veterinário é sempre quem decide. Responda em português do Brasil, em "
    "JSON com as chaves 'alertas' e 'sugestoes', cada uma sendo uma lista de "
    "strings curtas e objetivas."
)


class SugestoesResponse(BaseModel):
    alertas: List[str]
    sugestoes: List[str]
    aviso: str = AVISO_RESPONSABILIDADE


@app.post(
    "/consulta/{consulta_id}/sugestoes",
    response_model=SugestoesResponse,
    dependencies=[Depends(exigir_vet)],
)
def gerar_sugestoes(consulta_id: str):
    if not OPENAI_API_KEY:
        raise HTTPException(
            status_code=503,
            detail=(
                "OPENAI_API_KEY não configurada. Crie um arquivo .env em "
                "BACKEND/ a partir de .env.example e coloque sua chave."
            ),
        )

    trechos = transcricoes.get(consulta_id, [])
    if not trechos:
        raise HTTPException(
            status_code=404,
            detail="Ainda não há transcrição para esta consulta.",
        )

    from openai import OpenAI

    cliente = OpenAI(api_key=OPENAI_API_KEY)

    try:
        resposta = cliente.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": PROMPT_SISTEMA_SUGESTOES},
                {"role": "user", "content": " ".join(trechos)},
            ],
            response_format={"type": "json_object"},
        )
        dados = json.loads(resposta.choices[0].message.content)
    except Exception as erro:
        raise HTTPException(
            status_code=502,
            detail=f"Falha ao consultar o modelo de IA: {erro}",
        )

    return SugestoesResponse(
        alertas=dados.get("alertas", []),
        sugestoes=dados.get("sugestoes", []),
    )


# ---------------------------------------------------------------------------
# Prontuário automático (v0.5)
#
# Ao encerrar a consulta, gera um resumo clínico estruturado a partir da
# transcrição. O veterinário revisa/edita o texto na tela e salva; a versão
# final fica em BACKEND/prontuarios/<consulta_id>.txt.
# ---------------------------------------------------------------------------

# Na nuvem, aponte DATA_DIR para um disco persistente (senão os
# prontuários se perdem a cada novo deploy). Local: fica ao lado do app.
_DIR_PRONTUARIOS = Path(os.getenv("DATA_DIR", str(_DIR_DADOS))) / "prontuarios"

PROMPT_SISTEMA_PRONTUARIO = (
    "Você é um assistente que redige rascunhos de prontuário para um médico "
    "veterinário, a partir da transcrição de uma teleconsulta com o tutor do "
    "animal. Escreva em português do Brasil, em texto corrido organizado nas "
    "seções abaixo (use os títulos exatamente assim, um por linha, seguido do "
    "conteúdo):\n"
    "QUEIXA PRINCIPAL:\n"
    "HISTÓRICO RELATADO:\n"
    "SINAIS OBSERVADOS NA CONSULTA:\n"
    "HIPÓTESES DISCUTIDAS:\n"
    "TRATAMENTO A SEGUIR:\n"
    "ORIENTAÇÕES E PRÓXIMOS PASSOS:\n"
    "Regras: use apenas informações presentes na transcrição — não invente "
    "dados (se uma seção não tiver informação, escreva 'Não relatado'). Na "
    "seção TRATAMENTO A SEGUIR, registre exclusivamente o tratamento, "
    "medicações ou condutas que O PRÓPRIO VETERINÁRIO indicou verbalmente "
    "durante a conversa — nunca sugira nem acrescente tratamento por conta "
    "própria; se ele não indicou nada, escreva 'Não definido nesta consulta'. "
    "Não escreva diagnóstico definitivo; hipóteses são apenas as que foram "
    "discutidas na conversa. Este é um RASCUNHO que o veterinário vai "
    "revisar, corrigir e assinar — ele é o único responsável pelo conteúdo "
    "final."
)

AVISO_PRONTUARIO = (
    "Rascunho gerado por IA a partir da transcrição. Revise, corrija e "
    "complete antes de salvar — o conteúdo final é de responsabilidade do "
    "veterinário."
)


class GerarProntuarioRequest(BaseModel):
    anotacoes: str = ""


class ProntuarioResponse(BaseModel):
    consulta_id: str
    texto: str
    aviso: str = AVISO_PRONTUARIO


class SalvarProntuarioRequest(BaseModel):
    texto: str


class SalvarProntuarioResponse(BaseModel):
    consulta_id: str
    salvo_em: str


@app.post(
    "/consulta/{consulta_id}/prontuario",
    response_model=ProntuarioResponse,
    dependencies=[Depends(exigir_vet)],
)
def gerar_prontuario(consulta_id: str, corpo: GerarProntuarioRequest | None = None):
    if not OPENAI_API_KEY:
        raise HTTPException(
            status_code=503,
            detail=(
                "OPENAI_API_KEY não configurada. Crie um arquivo .env em "
                "BACKEND/ a partir de .env.example e coloque sua chave."
            ),
        )

    trechos = transcricoes.get(consulta_id, [])
    if not trechos:
        raise HTTPException(
            status_code=404,
            detail="Ainda não há transcrição para esta consulta.",
        )

    from openai import OpenAI

    cliente = OpenAI(api_key=OPENAI_API_KEY)

    info = consultas_info.get(consulta_id, {})
    identificacao = ""
    if info.get("nome_tutor") or info.get("nome_animal"):
        identificacao = (
            f"Dados da consulta — tutor: {info.get('nome_tutor') or 'não informado'}; "
            f"animal: {info.get('nome_animal') or 'não informado'}.\n\n"
        )

    anotacoes = (corpo.anotacoes.strip() if corpo else "")
    bloco_anotacoes = ""
    if anotacoes:
        bloco_anotacoes = (
            "\n\nANOTAÇÕES DIGITADAS PELO VETERINÁRIO DURANTE A CONSULTA "
            "(têm prioridade sobre a transcrição em caso de divergência, "
            "principalmente para doses e tratamento):\n" + anotacoes
        )

    try:
        resposta = cliente.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": PROMPT_SISTEMA_PRONTUARIO},
                {
                    "role": "user",
                    "content": identificacao + " ".join(trechos) + bloco_anotacoes,
                },
            ],
        )
        texto = resposta.choices[0].message.content.strip()
    except Exception as erro:
        raise HTTPException(
            status_code=502,
            detail=f"Falha ao consultar o modelo de IA: {erro}",
        )

    return ProntuarioResponse(consulta_id=consulta_id, texto=texto)


@app.put(
    "/consulta/{consulta_id}/prontuario",
    response_model=SalvarProntuarioResponse,
    dependencies=[Depends(exigir_vet)],
)
def salvar_prontuario(consulta_id: str, corpo: SalvarProntuarioRequest):
    """Salva a versão revisada pelo veterinário em prontuarios/<id>.txt."""
    texto = corpo.texto.strip()
    if not texto:
        raise HTTPException(status_code=422, detail="Prontuário vazio.")

    _DIR_PRONTUARIOS.mkdir(parents=True, exist_ok=True)

    momento = datetime.now(timezone.utc)
    info = consultas_info.get(consulta_id, {})

    # Nome de arquivo mais amigável quando temos tutor/animal:
    # ex. 2026-07-23_Rex_Joao_<id>.txt (mantém o id pra nunca colidir)
    partes_nome = [momento.astimezone().strftime("%Y-%m-%d")]
    for chave in ("nome_animal", "nome_tutor"):
        valor = info.get(chave, "")
        if valor:
            limpo = "".join(c for c in valor if c.isalnum() or c in " -_").strip()
            if limpo:
                partes_nome.append(limpo.replace(" ", "-"))
    partes_nome.append(consulta_id[:8])
    caminho = _DIR_PRONTUARIOS / ("_".join(partes_nome) + ".txt")

    linhas_id = []
    if info.get("nome_tutor"):
        linhas_id.append(f"Tutor: {info['nome_tutor']}")
    if info.get("nome_animal"):
        linhas_id.append(f"Animal: {info['nome_animal']}")
    if NOME_VETERINARIO:
        vet = f"Veterinário: Dr. {NOME_VETERINARIO}"
        if CRMV:
            vet += f" — {CRMV}"
        linhas_id.append(vet)
    if TELEFONE:
        linhas_id.append(f"Contato: {TELEFONE}")

    cabecalho = (
        f"{NOME_CLINICA} — Prontuário da consulta {consulta_id}\n"
        + ("\n".join(linhas_id) + "\n" if linhas_id else "")
        + f"Salvo em: {momento.astimezone().strftime('%d/%m/%Y %H:%M')}\n"
        f"{'-' * 60}\n\n"
    )
    caminho.write_text(cabecalho + texto + "\n", encoding="utf-8")

    logger.info("prontuário salvo em %s", caminho)
    return SalvarProntuarioResponse(
        consulta_id=consulta_id,
        salvo_em=momento.isoformat(),
    )


# ---------------------------------------------------------------------------
# Histórico de consultas (v0.5.1)
#
# Lista e exibe os prontuários salvos, pro veterinário consultar direto
# na tela — sem precisar abrir a pasta manualmente.
# ---------------------------------------------------------------------------

class ItemHistorico(BaseModel):
    arquivo: str
    titulo: str
    modificado_em: str


@app.get(
    "/prontuarios",
    response_model=List[ItemHistorico],
    dependencies=[Depends(exigir_vet)],
)
def listar_prontuarios():
    if not _DIR_PRONTUARIOS.is_dir():
        return []

    itens = []
    for caminho in sorted(
        _DIR_PRONTUARIOS.glob("*.txt"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):
        # Título legível a partir do nome do arquivo:
        # 2026-07-23_Rex_Joao_436c750f.txt -> "23/07/2026 — Rex (Joao)"
        nome = caminho.stem
        partes = nome.split("_")
        titulo = nome
        if len(partes) >= 2:
            data = partes[0]
            meio = partes[1:-1]  # descarta o id no final
            try:
                data_fmt = datetime.strptime(data, "%Y-%m-%d").strftime("%d/%m/%Y")
            except ValueError:
                data_fmt = data
            if len(meio) >= 2:
                titulo = f"{data_fmt} — {meio[0]} ({meio[1]})"
            elif len(meio) == 1:
                titulo = f"{data_fmt} — {meio[0]}"
            else:
                titulo = f"{data_fmt} — consulta {partes[-1]}"

        itens.append(ItemHistorico(
            arquivo=caminho.name,
            titulo=titulo,
            modificado_em=datetime.fromtimestamp(
                caminho.stat().st_mtime, tz=timezone.utc
            ).isoformat(),
        ))
    return itens


class ConteudoProntuario(BaseModel):
    arquivo: str
    texto: str


@app.get(
    "/prontuarios/{arquivo}",
    response_model=ConteudoProntuario,
    dependencies=[Depends(exigir_vet)],
)
def ler_prontuario(arquivo: str):
    # Proteção contra path traversal: só aceita nome simples .txt
    if "/" in arquivo or "\\" in arquivo or ".." in arquivo or not arquivo.endswith(".txt"):
        raise HTTPException(status_code=400, detail="Nome de arquivo inválido.")

    caminho = _DIR_PRONTUARIOS / arquivo
    if not caminho.is_file():
        raise HTTPException(status_code=404, detail="Prontuário não encontrado.")

    return ConteudoProntuario(
        arquivo=arquivo,
        texto=caminho.read_text(encoding="utf-8"),
    )