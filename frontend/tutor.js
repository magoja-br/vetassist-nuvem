// VetAssist AI - frontend v0.4 (papel: tutor)
// Página que o tutor abre pelo link enviado pelo veterinário.
// Recebe a oferta WebRTC do veterinário e responde.

// Mesma lógica do script.js: descobre o backend a partir de onde esta
// página foi aberta (funciona em 127.0.0.1 e no IP da rede local).
const BACKEND_HTTP =
    window.location.protocol === "http:" || window.location.protocol === "https:"
        ? window.location.origin
        : "http://127.0.0.1:8000";
const BACKEND_WS = BACKEND_HTTP.replace(/^http/, "ws");
const ICE_SERVERS = [{ urls: "stun:stun.l.google.com:19302" }];

// Personaliza a tela com o nome da clínica/veterinário (do .env do
// backend) e sauda o tutor/animal desta consulta pelo nome.
(async () => {
    try {
        const resp = await fetch(`${BACKEND_HTTP}/config`);
        if (!resp.ok) return;
        const cfg = await resp.json();
        if (cfg.nome_clinica) {
            document.getElementById("titulo-clinica").textContent = cfg.nome_clinica;
            document.title = cfg.nome_clinica;
        }
        if (cfg.nome_veterinario) {
            let sub = `Sala de atendimento do Dr. ${cfg.nome_veterinario}`;
            if (cfg.crmv) sub += ` — ${cfg.crmv}`;
            document.getElementById("subtitulo-clinica").textContent = sub;
        }
    } catch (e) {
        // sem backend, fica o nome padrão
    }

    // Saudação personalizada da consulta (João, Rex...)
    const consultaId = new URLSearchParams(window.location.search).get("consulta");
    if (!consultaId) return;
    try {
        const resp = await fetch(`${BACKEND_HTTP}/consulta/${consultaId}/info`);
        if (!resp.ok) return;
        const info = await resp.json();
        if (info.nome_tutor || info.nome_animal) {
            const ola = info.nome_tutor ? `Olá, ${info.nome_tutor}!` : "Olá!";
            const animal = info.nome_animal ? ` Consulta de ${info.nome_animal}.` : "";
            statusMsg.textContent = `${ola}${animal} Clique em "Entrar na Consulta" quando estiver pronto.`;
        }
    } catch (e) {
        // segue sem saudação
    }
})();

const btnEntrar = document.getElementById("btn-entrar");
const statusMsg = document.getElementById("status-msg");
const videoPlaceholder = document.getElementById("video-placeholder");
const localVideo = document.getElementById("local-video");
const remoteVideo = document.getElementById("remote-video");

let peerConnection = null;
let socket = null;
let localStream = null;

function pegarConsultaId() {
    const params = new URLSearchParams(window.location.search);
    return params.get("consulta");
}

function criarPeerConnection() {
    const pc = new RTCPeerConnection({ iceServers: ICE_SERVERS });

    pc.onicecandidate = (evento) => {
        if (evento.candidate) {
            enviarSinal({ tipo: "candidate", candidate: evento.candidate });
        }
    };

    pc.ontrack = (evento) => {
        remoteVideo.srcObject = evento.streams[0];
        videoPlaceholder.style.display = "none";
    };

    pc.onconnectionstatechange = () => {
        if (pc.connectionState === "connected") {
            statusMsg.textContent = "Conectado com o veterinário.";
        }
    };

    return pc;
}

function enviarSinal(mensagem) {
    if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify(mensagem));
    }
}

async function entrarNaConsulta() {
    const consultaId = pegarConsultaId();

    if (!consultaId) {
        statusMsg.textContent =
            "Link inválido: falta o identificador da consulta.";
        return;
    }

    btnEntrar.disabled = true;
    btnEntrar.textContent = "Conectando...";

    try {
        localStream = await navigator.mediaDevices.getUserMedia({
            video: true,
            audio: true,
        });
        localVideo.srcObject = localStream;

        socket = new WebSocket(
            `${BACKEND_WS}/ws/sala/${consultaId}?papel=tutor`
        );

        socket.onopen = () => {
            statusMsg.textContent = "Conectado à sala. Aguardando o veterinário...";
        };

        socket.onmessage = async (evento) => {
            const dados = JSON.parse(evento.data);

            switch (dados.tipo) {
                case "peer-entrou":
                    // O veterinário inicia a oferta; o tutor só aguarda.
                    statusMsg.textContent = "Veterinário na sala. Aguardando chamada...";
                    break;

                case "offer":
                    peerConnection = criarPeerConnection();
                    localStream.getTracks().forEach((track) =>
                        peerConnection.addTrack(track, localStream)
                    );
                    await peerConnection.setRemoteDescription(dados.sdp);
                    const answer = await peerConnection.createAnswer();
                    await peerConnection.setLocalDescription(answer);
                    enviarSinal({ tipo: "answer", sdp: answer });
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
                    statusMsg.textContent = "O veterinário saiu da chamada.";
                    remoteVideo.srcObject = null;
                    videoPlaceholder.style.display = "";
                    if (peerConnection) {
                        peerConnection.close();
                        peerConnection = null;
                    }
                    break;

                case "erro":
                    statusMsg.textContent = "Erro na sala: " + dados.mensagem;
                    break;
            }
        };

        socket.onerror = () => {
            statusMsg.textContent = "Erro na conexão com o backend.";
        };

        btnEntrar.textContent = "Consulta em andamento";
    } catch (erro) {
        statusMsg.textContent = "Erro ao entrar na consulta: " + erro.message;
        btnEntrar.disabled = false;
        btnEntrar.textContent = "Entrar na Consulta";
    }
}

btnEntrar.addEventListener("click", entrarNaConsulta);
