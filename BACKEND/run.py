import os

import uvicorn

if __name__ == "__main__":
    # Provedores de nuvem (Render, Railway, Fly, etc.) informam a porta
    # pela variável de ambiente PORT. Localmente, cai em 8000.
    porta = int(os.getenv("PORT", "8000"))

    # reload só em desenvolvimento (DEV=1); em produção fica desligado.
    dev = os.getenv("DEV") == "1"

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=porta,
        reload=dev,
        reload_dirs=["app"] if dev else None,
    )
