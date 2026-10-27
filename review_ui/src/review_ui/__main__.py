"""`python -m review_ui`: run the review UI with uvicorn on BVP_REVIEW_UI_PORT (8001)."""

import uvicorn

from review_ui.settings import ReviewUiSettings


def main() -> None:
    settings = ReviewUiSettings.from_environment()
    # 0.0.0.0 so Caddy can reach the container on the compose network.
    uvicorn.run(
        "review_ui.app:create_app",
        factory=True,
        host="0.0.0.0",
        port=settings.port,
        # Caddy sets X-Forwarded-*; access logs then show the real client.
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
