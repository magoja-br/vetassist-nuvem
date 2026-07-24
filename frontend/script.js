// VetAssist AI - frontend v0.4 (papel: veterinário)
// Conecta a tela ao backend (BACKEND/app/main.py), estabelece a
// videochamada com o tutor via WebRTC (backend só sinaliza), e agora
// também envia o áudio da chamada para transcrição local e permite
// gerar sugestões clínicas a partir do que foi transcrito.

// Descobre o endereço do backend automaticamente a partir de onde esta
// página foi aberta. Se veio de http://<ip>:8000/app/index.html (servido
// pelo próprio backend), usa esse mesmo endereço — funciona igual em
// 127.0.0.1 (notebook) e no IP da rede local (celular do tutor). Se a
// página foi aberta como arquivo local (file://), cai de volta para
// 127.0.0.1:8000 (útil pra testes rápidos no mesmo computador).
const BACKEND_HTTP =
    window.location.protocol === "http:" || window.location.protocol === "https:"
        ? window.location.origin
        : "http://127.0.0.1:8000";
const BACKEND_WS = BACKEND_HTTP.replace(/^http/, "ws");

// ---------------------------------------------------------------------------
// Autenticação do veterinário (usada quando hospedado na nuvem).
// O token é a própria senha; fica guardado no navegador e vai no
// cabeçalho Authorization das chamadas protegidas. Se o backend não
// exigir senha (uso local), nada disso aparece.
// ---------------------------------------------------------------------------
let vetToken = localStorage.getItem("vetToken") || "";

function authHeaders(extra) {
    const h = Object.assign({}, extra || {});
    if (vetToken) h["Authorization"] = "Bearer " + vetToken;
    return h;
}

// fetch que já inclui o token e, se o backend responder 401, reabre o login.
async function authFetch(url, opts) {
    opts = opts || {};
    opts.headers = authHeaders(opts.headers);
    const resp = await fetch(url, opts);
    if (resp.status === 401) {
        vetToken = "";
        localStorage.removeItem("vetToken");
        mostrarLogin("Sessão expirada. Entre novamente.");
        throw new Error("não autorizado");
    }
    return resp;
}

function mostrarLogin(mensagem) {
    const overlay = document.getElementById("login-overlay");
    overlay.style.display = "flex";
    document.getElementById("login-erro").textContent = mensagem || "";
    document.getElementById("login-senha").focus();
}

function esconderLogin() {
    document.getElementById("login-overlay").style.display = "none";
}

async function verificarLogin() {
    // Descobre se o backend exige senha e, se sim, valida o token guardado.
    let cfg;
    try {
        const r = await fetch(`${BACKEND_HTTP}/config`);
        cfg = await r.json();
    } catch (e) {
        return; // sem backend, verificarBackend() já avisa
    }

    if (cfg.nome_clinica) {
        const el = document.getElementById("login-titulo");
        if (el) el.textContent = cfg.nome_clinica;
    }

    if (!cfg.auth_required) {
        esconderLogin();
        return; // uso local: sem senha
    }

    if (vetToken) {
        const ok = await fetch(`${BACKEND_HTTP}/auth/verificar`, {
            headers: authHeaders(),
        }).then((r) => r.ok).catch(() => false);
        if (ok) { esconderLogin(); return; }
    }
    mostrarLogin();
}

async function fazerLogin() {
    const senha = document.getElementById("login-senha").value;
    if (!senha) return;
    const ok = await fetch(`${BACKEND_HTTP}/auth/verificar`, {
        headers: { Authorization: "Bearer " + senha },
    }).then((r) => r.ok).catch(() => false);

    if (ok) {
        vetToken = senha;
        localStorage.setItem("vetToken", senha);
        esconderLogin();
    } else {
        document.getElementById("login-erro").textContent = "Senha incorreta.";
    }
}

// Servidores ICE (STUN/TURN). Começa com um padrão e é substituído pelos
// que o backend informa em /config (inclui TURN, essencial pra celulares
// em redes diferentes).
let ICE_SERVERS = [{ urls: "stun:stun.l.google.com:19302" }];
// Trechos maiores cortam menos palavras no meio e dão mais contexto ao
// modelo — melhora a fluência da transcrição (custo: texto aparece com
// um pouco mais de atraso no painel).
const DURACAO_TRECHO_AUDIO_MS = 10000;

const statusBadge = document.getElementById("status-badge");
const btnIniciar = document.getElementById("btn-iniciar");
const consultaInfo = document.getElementById("consulta-info");
const painelTranscricao = document.getElementById("painel-transcricao");
const painelIA = document.getElementById("painel-ia");
const videoPlaceholder = document.getElementById("video-placeholder");
const localVideo = document.getElementById("local-video");
const remoteVideo = document.getElementById("remote-video");
const linkTutorBox = document.getElementById("link-tutor");
const linkTutorInput = document.getElementById("link-tutor-input");
const btnCopiarLink = document.getElementById("btn-copiar-link");
const btnSugestoes = document.getElementById("btn-sugestoes");
const sugestoesResultado = document.getElementById("sugestoes-resultado");
const btnEncerrar = document.getElementById("btn-encerrar");
const prontuarioArea = document.getElementById("prontuario-area");
const prontuarioTexto = document.getElementById("prontuario-texto");
const prontuarioAviso = document.getElementById("prontuario-aviso");
const btnSalvarProntuario = document.getElementById("btn-salvar-prontuario");
const prontuarioStatus = document.getElementById("prontuario-status");

let peerConnection = null;
let socket = null;
let localStream = null;
let remoteStream = null;
let consultaIdAtual = null;
let wsTranscricao = null;
let transcricaoIniciada = false;

function setBadge(state, texto) {
    statusBadge.className = state;
    statusBadge.textContent = texto;
}

async function aplicarMarca() {
    // Personaliza a tela com o nome da clínica/veterinário definido no
    // .env do backend (NOME_CLINICA / NOME_VETERINARIO).
    try {
        const resp = await fetch(`${BACKEND_HTTP}/config`);
        if (!resp.ok) return;
        const cfg = await resp.json();
        if (cfg.nome_clinica) {
            document.getElementById("titulo-clinica").textContent = cfg.nome_clinica;
            document.title = cfg.nome_clinica;
        }
        if (cfg.subtitulo) {
            document.getElementById("subtitulo-clinica").textContent = cfg.subtitulo;
        }
        if (Array.isArray(cfg.ice_servers) && cfg.ice_servers.length) {
            ICE_SERVERS = cfg.ice_servers;
        }
    } catch (e) {
        // sem backend, fica o nome padrão
    }
}

async function verificarBackend() {
    try {
        const resp = await fetch(`${BACKEND_HTTP}/health`);
        if (!resp.ok) throw new Error("resposta não ok");
        setBadge("online", "backend online");
        btnIniciar.disabled = false;
    } catch (erro) {
        setBadge("offline", "backend offline");
        btnIniciar.disabled = true;
        consultaInfo.textContent =
            "Não foi possível conectar ao backend em " + BACKEND_HTTP +
            ". Verifique se ele está rodando (python run.py).";
    }
}

async function linkDoTutor(consultaId) {
    const hostAtual = window.location.hostname;
    const ehLocalhost = hostAtual === "127.0.0.1" || hostAtual === "localhost";

    if (!ehLocalhost) {
        // Já estamos em um endereço acessível de fora (IP da rede local,
        // ou um túnel tipo ngrok) — usa a própria origem da página, sem
        // trocar nada. Isso é o caminho certo quando o veterinário abriu
        // a tela por um link https:// (ngrok), por exemplo.
        return `${window.location.origin}/app/tutor.html?consulta=${consultaId}`;
    }

    // Página aberta como 127.0.0.1/localhost (comum no notebook, pra
    // liberar câmera sem precisar de HTTPS): busca o IP de rede real no
    // backend (GET /config) pra montar um link que funcione em outro
    // dispositivo na mesma rede Wi-Fi.
    let host = hostAtual;
    try {
        const resp = await fetch(`${BACKEND_HTTP}/config`);
        if (resp.ok) {
            const dados = await resp.json();
            if (dados.lan_ip) host = dados.lan_ip;
        }
    } catch (e) {
        console.warn("Não foi possível obter o IP de rede do backend, usando o host atual.", e);
    }

    return `http://${host}:8000/app/tutor.html?consulta=${consultaId}`;
}

function criarPeerConnection() {
    const pc = new RTCPeerConnection({ iceServers: ICE_SERVERS });

    pc.onicecandidate = (evento) => {
        if (evento.candidate) {
            enviarSinal({ tipo: "candidate", candidate: evento.candidate });
        }
    };

    pc.ontrack = (evento) => {
        remoteStream = evento.streams[0];
        remoteVideo.srcObject = remoteStream;
        videoPlaceholder.style.display = "none";
    };

    pc.onconnectionstatechange = () => {
        if (pc.connectionState === "connected") {
            painelTranscricao.querySelector("p").textContent =
                "Chamada conectada. Transcrevendo localmente...";
            iniciarTranscricaoSeProntoPara(consultaIdAtual);
        }
    };

    return pc;
}

async function criarStreamMixado(streamA, streamB) {
    const audioContext = new AudioContext();

    // Em alguns navegadores o AudioContext nasce "suspenso" (política de
    // autoplay) e o áudio processado fica mudo até isto ser chamado.
    if (audioContext.state === "suspended") {
        await audioContext.resume();
    }

    const destino = audioContext.createMediaStreamDestination();
    let faixasConectadas = 0;

    [streamA, streamB].forEach((stream) => {
        const faixasAudio = stream.getAudioTracks();
        if (faixasAudio.length === 0) return;
        const origem = audioContext.createMediaStreamSource(
            new MediaStream(faixasAudio)
        );
        origem.connect(destino);
        faixasConectadas += 1;
    });

    console.log(
        `Mixagem de áudio: ${faixasConectadas} faixa(s) conectada(s), ` +
        `AudioContext state=${audioContext.state}`
    );

    return destino.stream;
}

function adicionarTranscricao(texto) {
    const p = painelTranscricao.querySelector("p");
    const vazio =
        p.textContent.startsWith("A transcrição") ||
        p.textContent.startsWith("Chamada conectada") ||
        p.textContent.startsWith("Aguardando") ||
        p.textContent.startsWith("Gravando primeiro trecho");
    p.textContent = vazio ? texto : `${p.textContent} ${texto}`;
    painelTranscricao.scrollTop = painelTranscricao.scrollHeight;
}

async function iniciarTranscricaoSeProntoPara(consultaId) {
    if (transcricaoIniciada || !consultaId || !localStream || !remoteStream) {
        return;
    }
    transcricaoIniciada = true;

    const streamMixado = await criarStreamMixado(localStream, remoteStream);
    wsTranscricao = new WebSocket(`${BACKEND_WS}/ws/transcricao/${consultaId}`);

    wsTranscricao.onmessage = (evento) => {
        const dados = JSON.parse(evento.data);
        if (dados.tipo === "transcricao" && dados.texto) {
            adicionarTranscricao(dados.texto);
        } else if (dados.tipo === "erro") {
            console.error("Erro na transcrição:", dados.mensagem);
            adicionarTranscricao(`[erro na transcrição: ${dados.mensagem}]`);
        }
    };

    wsTranscricao.onerror = () => {
        adicionarTranscricao(
            "[não foi possível conectar ao serviço de transcrição]"
        );
    };

    wsTranscricao.onopen = () => {
        adicionarTranscricao("Gravando primeiro trecho de áudio...");
        gravarProximoTrecho(streamMixado);
    };
}

function gravarProximoTrecho(streamMixado) {
    if (!wsTranscricao || wsTranscricao.readyState !== WebSocket.OPEN) return;

    let recorder;
    try {
        recorder = new MediaRecorder(streamMixado, {
            mimeType: "audio/webm;codecs=opus",
        });
    } catch (e) {
        console.warn("MediaRecorder não suportado para transcrição:", e);
        return;
    }

    const partes = [];
    recorder.ondataavailable = (e) => {
        if (e.data.size > 0) partes.push(e.data);
    };

    recorder.onstop = async () => {
        const blob = new Blob(partes, { type: "audio/webm" });
        if (blob.size > 1000 && wsTranscricao.readyState === WebSocket.OPEN) {
            wsTranscricao.send(await blob.arrayBuffer());
        }
        gravarProximoTrecho(streamMixado); // encadeia o próximo trecho
    };

    recorder.start();
    setTimeout(() => {
        if (recorder.state !== "inactive") recorder.stop();
    }, DURACAO_TRECHO_AUDIO_MS);
}

function enviarSinal(mensagem) {
    if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify(mensagem));
    }
}

async function conectarSinalizacao(consultaId) {
    socket = new WebSocket(`${BACKEND_WS}/ws/sala/${consultaId}?papel=vet`);

    socket.onopen = () => {
        painelIA.querySelector("p").textContent =
            "Aguardando o tutor entrar na sala...";
    };

    socket.onmessage = async (evento) => {
        const dados = JSON.parse(evento.data);

        switch (dados.tipo) {
            case "peer-entrou":
                // O veterinário sempre inicia a oferta.
                peerConnection = criarPeerConnection();
                localStream.getTracks().forEach((track) =>
                    peerConnection.addTrack(track, localStream)
                );
                const offer = await peerConnection.createOffer();
                await peerConnection.setLocalDescription(offer);
                enviarSinal({ tipo: "offer", sdp: offer });
                painelIA.querySelector("p").textContent =
                    "Tutor conectado. Estabelecendo videochamada...";
                break;

            case "answer":
                if (peerConnection) {
                    await peerConnection.setRemoteDescription(dados.sdp);
                }
                break;

            case "candidate":
                if (peerConnection) {
                    try {
                        await peerConnection.addIceCandidate(dados.candidate);
                    } catch (e) {
                        console.warn("Erro ao adicionar ICE candidate", e);
                    }
                }
                break;

            case "peer-saiu":
                painelIA.querySelector("p").textContent =
                    "O tutor saiu da chamada.";
                remoteVideo.srcObject = null;
                videoPlaceholder.style.display = "";
                if (peerConnection) {
                    peerConnection.close();
                    peerConnection = null;
                }
                break;

            case "erro":
                consultaInfo.textContent = "Erro na sala: " + dados.mensagem;
                break;
        }
    };

    socket.onerror = () => {
        consultaInfo.textContent =
            "Erro na conexão de sinalização com o backend.";
    };
}

async function iniciarConsulta() {
    btnIniciar.disabled = true;
    btnIniciar.textContent = "Iniciando...";
    consultaInfo.textContent = "";

    const nomeTutor = document.getElementById("input-tutor").value.trim();
    const nomeAnimal = document.getElementById("input-animal").value.trim();

    try {
        const resp = await authFetch(`${BACKEND_HTTP}/consulta/iniciar`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                nome_tutor: nomeTutor,
                nome_animal: nomeAnimal,
            }),
        });
        if (!resp.ok) throw new Error(`erro HTTP ${resp.status}`);
        const dados = await resp.json();

        const identificacao =
            nomeTutor || nomeAnimal
                ? `${nomeAnimal || "animal"} (tutor: ${nomeTutor || "não informado"}) — `
                : "";
        consultaInfo.textContent =
            `${identificacao}consulta iniciada em ` +
            `${new Date(dados.iniciada_em).toLocaleString("pt-BR")}`;

        consultaIdAtual = dados.consulta_id;

        linkTutorInput.value = "detectando endereço da rede...";
        linkTutorBox.style.display = "block";
        linkTutorInput.value = await linkDoTutor(dados.consulta_id);

        localStream = await navigator.mediaDevices.getUserMedia({
            video: true,
            audio: true,
        });
        localVideo.srcObject = localStream;

        await conectarSinalizacao(dados.consulta_id);

        btnIniciar.textContent = "Consulta em andamento";
        btnSugestoes.disabled = false;
        btnEncerrar.disabled = false;
    } catch (erro) {
        consultaInfo.textContent = "Erro ao iniciar a consulta: " + erro.message;
        btnIniciar.disabled = false;
        btnIniciar.textContent = "Iniciar Consulta";
    }
}

btnCopiarLink.addEventListener("click", async () => {
    linkTutorInput.select();
    try {
        await navigator.clipboard.writeText(linkTutorInput.value);
        btnCopiarLink.textContent = "Copiado!";
        setTimeout(() => (btnCopiarLink.textContent = "Copiar"), 1500);
    } catch (e) {
        // clipboard API pode falhar em file:// sem permissão; o campo
        // já fica selecionado para copiar manualmente (Ctrl+C).
    }
});

btnSugestoes.addEventListener("click", async () => {
    if (!consultaIdAtual) return;

    btnSugestoes.disabled = true;
    btnSugestoes.textContent = "Gerando...";

    try {
        const resp = await authFetch(
            `${BACKEND_HTTP}/consulta/${consultaIdAtual}/sugestoes`,
            { method: "POST" }
        );
        const dados = await resp.json();

        if (!resp.ok) {
            throw new Error(dados.detail || `erro HTTP ${resp.status}`);
        }

        let html = "";
        if (dados.alertas && dados.alertas.length) {
            html +=
                "<strong>Sinais de atenção:</strong><ul>" +
                dados.alertas.map((a) => `<li>${a}</li>`).join("") +
                "</ul>";
        }
        if (dados.sugestoes && dados.sugestoes.length) {
            html +=
                "<strong>Sugestões:</strong><ul>" +
                dados.sugestoes.map((s) => `<li>${s}</li>`).join("") +
                "</ul>";
        }
        html += `<p style="font-size:11px;color:#888;">${dados.aviso}</p>`;

        sugestoesResultado.innerHTML = html;
    } catch (erro) {
        sugestoesResultado.textContent = "Erro ao gerar sugestões: " + erro.message;
    } finally {
        btnSugestoes.disabled = false;
        btnSugestoes.textContent = "Gerar Sugestões";
    }
});

function encerrarMidia() {
    // Para a gravação/transcrição e a chamada, mas mantém a página aberta
    // pra revisão do prontuário.
    if (wsTranscricao && wsTranscricao.readyState === WebSocket.OPEN) {
        wsTranscricao.close();
    }
    if (peerConnection) {
        peerConnection.close();
        peerConnection = null;
    }
    if (socket && socket.readyState === WebSocket.OPEN) {
        socket.close();
    }
    if (localStream) {
        localStream.getTracks().forEach((t) => t.stop());
    }
}

btnEncerrar.addEventListener("click", async () => {
    if (!consultaIdAtual) return;

    btnEncerrar.disabled = true;
    btnEncerrar.textContent = "Gerando prontuário...";

    encerrarMidia();

    const anotacoes = document.getElementById("anotacoes-consulta").value.trim();

    try {
        const resp = await authFetch(
            `${BACKEND_HTTP}/consulta/${consultaIdAtual}/prontuario`,
            {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ anotacoes: anotacoes }),
            }
        );
        const dados = await resp.json();

        if (!resp.ok) {
            throw new Error(dados.detail || `erro HTTP ${resp.status}`);
        }

        prontuarioTexto.value = dados.texto;
        prontuarioAviso.textContent = dados.aviso;
        prontuarioArea.style.display = "block";
        btnEncerrar.textContent = "Consulta encerrada";
    } catch (erro) {
        prontuarioArea.style.display = "block";
        prontuarioTexto.value = "";
        prontuarioAviso.textContent =
            "Não foi possível gerar o rascunho automaticamente (" +
            erro.message +
            "). Você pode escrever o prontuário manualmente aqui e salvar.";
        btnEncerrar.textContent = "Consulta encerrada";
    }
});

btnSalvarProntuario.addEventListener("click", async () => {
    if (!consultaIdAtual) return;

    btnSalvarProntuario.disabled = true;
    prontuarioStatus.textContent = "";

    try {
        const resp = await authFetch(
            `${BACKEND_HTTP}/consulta/${consultaIdAtual}/prontuario`,
            {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ texto: prontuarioTexto.value }),
            }
        );
        const dados = await resp.json();

        if (!resp.ok) {
            throw new Error(dados.detail || `erro HTTP ${resp.status}`);
        }

        prontuarioStatus.textContent = "Salvo com sucesso.";
    } catch (erro) {
        prontuarioStatus.textContent = "Erro ao salvar: " + erro.message;
        prontuarioStatus.style.color = "#c62828";
    } finally {
        btnSalvarProntuario.disabled = false;
    }
});

// ---------------------------------------------------------------------------
// Histórico de consultas anteriores
// ---------------------------------------------------------------------------

const listaHistorico = document.getElementById("lista-historico");
const buscaHistorico = document.getElementById("busca-historico");
const visualizacaoHistorico = document.getElementById("visualizacao-historico");
const textoHistorico = document.getElementById("texto-historico");

let itensHistorico = [];

function abrirWhatsApp(texto) {
    // Abre o WhatsApp (Web ou app) com o texto pronto — o veterinário só
    // escolhe a conversa do tutor e envia.
    const url = "https://wa.me/?text=" + encodeURIComponent(texto);
    window.open(url, "_blank");
}

function desenharHistorico() {
    const filtro = buscaHistorico.value.trim().toLowerCase();
    const visiveis = itensHistorico.filter((item) =>
        item.titulo.toLowerCase().includes(filtro)
    );

    if (visiveis.length === 0) {
        listaHistorico.innerHTML =
            "<p>" +
            (itensHistorico.length === 0
                ? "Nenhuma consulta salva ainda."
                : "Nada encontrado com essa busca.") +
            "</p>";
        return;
    }

    listaHistorico.innerHTML = "";
    visiveis.forEach((item) => {
        const div = document.createElement("div");
        div.className = "item-historico";
        div.textContent = item.titulo;
        div.addEventListener("click", () => abrirProntuarioSalvo(item.arquivo));
        listaHistorico.appendChild(div);
    });
}

async function carregarHistorico() {
    try {
        const resp = await authFetch(`${BACKEND_HTTP}/prontuarios`);
        if (!resp.ok) throw new Error(`erro HTTP ${resp.status}`);
        itensHistorico = await resp.json();
        desenharHistorico();
    } catch (erro) {
        listaHistorico.innerHTML =
            "<p>Não foi possível carregar o histórico.</p>";
    }
}

async function abrirProntuarioSalvo(arquivo) {
    try {
        const resp = await authFetch(
            `${BACKEND_HTTP}/prontuarios/${encodeURIComponent(arquivo)}`
        );
        if (!resp.ok) throw new Error(`erro HTTP ${resp.status}`);
        const dados = await resp.json();
        textoHistorico.textContent = dados.texto;
        visualizacaoHistorico.style.display = "block";
        visualizacaoHistorico.scrollIntoView({ behavior: "smooth" });
    } catch (erro) {
        textoHistorico.textContent = "Erro ao abrir o prontuário: " + erro.message;
        visualizacaoHistorico.style.display = "block";
    }
}

buscaHistorico.addEventListener("input", desenharHistorico);

document.getElementById("btn-fechar-historico").addEventListener("click", () => {
    visualizacaoHistorico.style.display = "none";
});

document.getElementById("btn-copiar-historico").addEventListener("click", async () => {
    try {
        await navigator.clipboard.writeText(textoHistorico.textContent);
    } catch (e) {
        // clipboard pode falhar sem HTTPS; sem problema
    }
});

document.getElementById("btn-whatsapp-historico").addEventListener("click", () => {
    abrirWhatsApp(textoHistorico.textContent);
});

document.getElementById("btn-whatsapp-prontuario").addEventListener("click", () => {
    abrirWhatsApp(prontuarioTexto.value);
});

// Recarrega o histórico depois que um prontuário novo é salvo
btnSalvarProntuario.addEventListener("click", () => {
    setTimeout(carregarHistorico, 1500);
});

btnIniciar.addEventListener("click", iniciarConsulta);

// Login
document.getElementById("login-btn").addEventListener("click", fazerLoginEIniciar);
document.getElementById("login-senha").addEventListener("keydown", (e) => {
    if (e.key === "Enter") fazerLoginEIniciar();
});

async function fazerLoginEIniciar() {
    await fazerLogin();
    const logado =
        document.getElementById("login-overlay").style.display === "none";
    if (logado) carregarHistorico();
}

aplicarMarca();
verificarBackend();

// Fluxo de entrada: valida login (se o backend exigir senha) e só então
// carrega o histórico (que é protegido).
(async () => {
    await verificarLogin();
    const precisaLogin =
        document.getElementById("login-overlay").style.display === "flex";
    if (!precisaLogin) carregarHistorico();
})();
