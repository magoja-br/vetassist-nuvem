# VetAssist AI — Publicar na nuvem (versão celular)

Esta é a versão para **uso no celular**, hospedada num servidor sempre
disponível — o veterinário e o tutor usam pelo navegador do celular, de
qualquer lugar, sem depender de um notebook ligado nem do ngrok.

O código já está pronto (`Dockerfile` na raiz). Falta apenas publicar num
provedor de nuvem, o que exige a **sua conta** lá (não dá para fazer isso
por você). Abaixo, o passo a passo.

---

## Antes de tudo: privacidade (LGPD)

Prontuários são **dados de saúde**. Ao guardá-los num servidor na
internet, você passa a ser responsável por protegê-los. Duas
recomendações:

1. Prefira um provedor com **servidor no Brasil** (menor exposição e
   melhor conformidade com a LGPD). O **Fly.io** tem região em São Paulo
   (`gru`).
2. Se preferir não guardar prontuários no servidor, é possível desligar o
   histórico e fazer o veterinário salvar/enviar cada prontuário na hora
   (por WhatsApp, como já funciona). Me avise se quiser essa variação.

Não sou advogado — para uso profissional real, vale uma orientação
jurídica sobre LGPD e prontuário eletrônico veterinário.

---

## Variáveis de ambiente (valem para qualquer provedor)

No painel do provedor, cadastre estas variáveis (equivalem ao `.env`):

| Variável | Valor |
|---|---|
| `OPENAI_API_KEY` | sua chave da OpenAI |
| `VET_SENHA` | **uma senha forte** (o vet usa para entrar) |
| `TRANSCRICAO` | `nuvem` |
| `NOME_CLINICA` | Dr. Maurício Junqueira de Sousa - Veterinário |
| `NOME_VETERINARIO` | Maurício Junqueira de Sousa |
| `CRMV` | CRMV-SP 4815 |
| `TELEFONE` | (15) 99744-8191 |
| `DATA_DIR` | `/data` (se o provedor oferecer disco persistente) |

`VET_SENHA` é obrigatória na nuvem: sem ela, qualquer um com o endereço
abriria a tela do veterinário, veria os prontuários e gastaria a sua
chave da OpenAI. Com ela, a tela do vet pede senha; a do tutor não (ele
entra só pelo link da consulta).

---

## Opção A — Render.com (mais fácil)

1. Suba a pasta `24_Consultorio` para um repositório no GitHub (privado).
2. Crie conta em https://render.com e clique em **New → Web Service**.
3. Conecte o repositório. O Render detecta o `Dockerfile` sozinho.
4. Em **Environment**, cadastre as variáveis da tabela acima.
5. Para guardar prontuários, adicione um **Disk** (ex.: montar em `/data`)
   e defina `DATA_DIR=/data`.
6. Clique em **Create Web Service**. Em alguns minutos você recebe um
   endereço `https://seu-app.onrender.com`.
7. Pronto: o veterinário abre `https://seu-app.onrender.com/app/index.html`
   no celular, digita a senha e usa. O link do tutor já sai com esse
   mesmo `https`.

Observação: o plano gratuito do Render "hiberna" quando fica ocioso (a
primeira consulta do dia demora ~30s para acordar). O plano pago (~US$ 7/
mês) fica sempre no ar. Servidores nos EUA (ver seção LGPD).

## Opção B — Fly.io (servidor em São Paulo, melhor p/ LGPD)

1. Crie conta em https://fly.io e instale o `flyctl` (linha de comando).
2. Na pasta `24_Consultorio`, rode `fly launch` (ele lê o `Dockerfile`);
   escolha a região **`gru` (São Paulo)**.
3. Cadastre os segredos:
   `fly secrets set OPENAI_API_KEY=... VET_SENHA=... TRANSCRICAO=nuvem ...`
4. Crie um volume persistente e aponte `DATA_DIR` para ele.
5. `fly deploy`. Você recebe um endereço `https://seu-app.fly.dev`.

Fly é um pouco mais técnico que o Render, mas roda no Brasil.

---

## Depois de publicado — uso diário

Nada de `.bat`, ngrok ou notebook. O veterinário:

1. Abre `https://<seu-endereço>/app/index.html` no celular (pode salvar
   como atalho na tela inicial).
2. Digita a senha (`VET_SENHA`) uma vez — fica lembrada no aparelho.
3. Preenche tutor/animal, inicia a consulta, envia o link ao tutor por
   WhatsApp, atende, gera e salva o prontuário — igual à versão notebook,
   mas tudo no celular.

## Custos aproximados

- Hospedagem: grátis (Render, com hibernação) a ~US$ 5–7/mês (sempre no ar).
- OpenAI: centavos por consulta (transcrição + sugestões + prontuário).
