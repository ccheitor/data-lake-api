"""Optional Locust alternative to k6 (pip install locust).

    locust -f loadtest/locustfile.py --host http://localhost:8000 --headless -u 20 -r 5 -t 60s
Tag-select a mode with  --tags cached  or  --tags direct.
"""
import os
import random

from locust import HttpUser, between, tag, task

KEY = os.getenv("API_KEY", "dev-analyst-key")
HOT = int(os.getenv("HOT_CUSTOMERS", "1000"))


class ApiUser(HttpUser):
    wait_time = between(0, 0)

    @tag("cached")
    @task
    def cached_lookup(self):
        cid = random.randint(1, HOT)
        self.client.get(f"/v1/customers/{cid}/transactions?limit=20", headers={"X-API-Key": KEY},
                        name="/v1/customers/[id]/transactions (cached)")

    @tag("direct")
    @task
    def direct_lookup(self):
        cid = random.randint(1, HOT)
        self.client.get(f"/v1/customers/{cid}/transactions?limit=20&cache=false", headers={"X-API-Key": KEY},
                        name="/v1/customers/[id]/transactions (direct Trino)")
