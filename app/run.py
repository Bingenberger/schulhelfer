"""Fixed one-worker runtime. No reload, arbitrary worker count or access log."""
import ipaddress
import uvicorn
from app.config import settings

def main():
    addresses = settings.PROXY_IPS.split(",") if settings.PROXY_IPS else []
    for address in addresses:
        ipaddress.ip_address(address)
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, workers=1,
                proxy_headers=bool(addresses), forwarded_allow_ips=addresses,
                access_log=False, timeout_graceful_shutdown=120)

if __name__ == "__main__":
    main()
