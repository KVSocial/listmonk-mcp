import pytest

import listmonk_mcp.server as server


class FakeClient:
    def __init__(self) -> None:
        self.list_requests: list[dict[str, object]] = []
        self.resolved_ids: list[int] = []
        self.campaign_request: dict[str, object] | None = None

    async def get_lists(self, **kwargs: object) -> dict[str, object]:
        self.list_requests.append(kwargs)
        return {
            "data": {
                "results": [
                    {
                        "id": 7,
                        "name": "Weekly News",
                        "uuid": "weekly-uuid",
                        "subscriber_count": 12,
                        "status": "active",
                        "type": "public",
                    }
                ],
                "total": 588,
                "page": 1,
                "per_page": 50,
            }
        }

    async def get_list(self, list_id: int) -> dict[str, object]:
        self.resolved_ids.append(list_id)
        return {"data": {"id": list_id, "name": f"List {list_id}"}}

    async def create_campaign(self, **kwargs: object) -> dict[str, object]:
        self.campaign_request = kwargs
        return {"data": {"id": 101}}


@pytest.mark.asyncio
async def test_get_mailing_lists_defaults_to_100_per_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeClient()
    monkeypatch.setattr(server, "get_client", lambda: fake)

    result = await server.get_mailing_lists()

    assert "Found 588 mailing lists" in result
    assert fake.list_requests == [
        {
            "page": 1,
            "per_page": 100,
            "query": None,
            "status": None,
        }
    ]


@pytest.mark.asyncio
async def test_search_mailing_lists_uses_server_side_name_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeClient()
    monkeypatch.setattr(server, "get_client", lambda: fake)

    result = await server.search_mailing_lists("  Weekly  ")

    assert "Found 588 mailing lists matching 'Weekly'" in result
    assert "ID: 7" in result
    assert fake.list_requests == [
        {
            "page": 1,
            "per_page": 100,
            "query": "Weekly",
            "status": "active",
            "order_by": "name",
            "order": "ASC",
        }
    ]


@pytest.mark.asyncio
async def test_create_campaign_resolves_targets_before_posting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeClient()
    monkeypatch.setattr(server, "get_client", lambda: fake)

    result = await server.create_campaign(
        name="Weekly campaign",
        subject="This week",
        lists=[7, 8],
        body="Hello",
    )

    assert "Successfully created campaign 'Weekly campaign' (ID: 101)" in result
    assert "List 7 (ID: 7), List 8 (ID: 8)" in result
    assert fake.resolved_ids == [7, 8]
    assert fake.campaign_request is not None
    assert fake.campaign_request["lists"] == [7, 8]


@pytest.mark.asyncio
async def test_create_campaign_rejects_empty_or_invalid_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeClient()
    monkeypatch.setattr(server, "get_client", lambda: fake)

    result = await server.create_campaign(
        name="Invalid campaign",
        subject="No target",
        lists=[0],
        body="Hello",
    )

    assert result == "Error: Mailing list IDs must be positive integers."
    assert fake.resolved_ids == []
    assert fake.campaign_request is None


class EstimateClient(FakeClient):
    def __init__(self, supports_estimate: bool = True) -> None:
        super().__init__()
        self.supports_estimate = supports_estimate
        self.estimate_ids: list[int] | None = None
        self.estimate_type: str | None = None

    async def get_recipient_estimate(
        self, list_ids: list[int], campaign_type: str = "regular"
    ) -> dict[str, object]:
        if not self.supports_estimate:
            raise RuntimeError("404")
        self.estimate_ids = list_ids
        self.estimate_type = campaign_type
        return {"data": {"count": 5000}}


@pytest.mark.asyncio
async def test_create_campaign_passes_daily_limit_and_reports_estimate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = EstimateClient()
    monkeypatch.setattr(server, "get_client", lambda: fake)

    result = await server.create_campaign(
        name="Daily", subject="Hi", lists=[7, 8], daily_limit=500
    )

    assert fake.campaign_request is not None
    assert fake.campaign_request["daily_limit"] == 500
    assert fake.estimate_ids == [7, 8]
    assert "Approx. 5,000 recipients" in result
    assert "about 10 day(s)" in result


@pytest.mark.asyncio
async def test_create_campaign_works_without_estimate_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = EstimateClient(supports_estimate=False)
    monkeypatch.setattr(server, "get_client", lambda: fake)

    result = await server.create_campaign(name="Daily", subject="Hi", lists=[7], daily_limit=500)

    assert "Successfully created campaign" in result
    assert "Daily limit: 500/day" in result


@pytest.mark.asyncio
async def test_create_campaign_rejects_negative_daily_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = EstimateClient()
    monkeypatch.setattr(server, "get_client", lambda: fake)

    result = await server.create_campaign(name="X", subject="Hi", lists=[7], daily_limit=-1)

    assert "daily_limit must be" in result
    assert fake.campaign_request is None


@pytest.mark.asyncio
async def test_estimate_campaign_recipients(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = EstimateClient()
    monkeypatch.setattr(server, "get_client", lambda: fake)

    result = await server.estimate_campaign_recipients(lists=[1, 2, 3], daily_limit=1000)

    assert fake.estimate_ids == [1, 2, 3]
    assert result == "Approx. 5,000 recipients; at 1,000/day this takes about 5 day(s)"


@pytest.mark.asyncio
async def test_create_campaign_estimates_with_campaign_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = EstimateClient()
    monkeypatch.setattr(server, "get_client", lambda: fake)

    await server.create_campaign(name="Opt-in", subject="Confirm", lists=[7], type="optin")
    assert fake.estimate_type == "optin"

    await server.create_campaign(name="News", subject="Hi", lists=[7])
    assert fake.estimate_type == "regular"


@pytest.mark.asyncio
async def test_estimate_campaign_recipients_passes_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = EstimateClient()
    monkeypatch.setattr(server, "get_client", lambda: fake)

    await server.estimate_campaign_recipients(lists=[1], type="optin")
    assert fake.estimate_type == "optin"

    result = await server.estimate_campaign_recipients(lists=[1], type="weekly")
    assert result == "Error: type must be 'regular' or 'optin'."


@pytest.mark.asyncio
async def test_client_sends_estimate_type(monkeypatch: pytest.MonkeyPatch) -> None:
    from listmonk_mcp.client import ListmonkClient

    calls: list[tuple[object, ...]] = []

    async def fake_request(self: object, method: str, endpoint: str, params: object = None,
                           json_data: object = None) -> dict[str, object]:
        calls.append((method, endpoint, params))
        return {"data": {"count": 3}}

    monkeypatch.setattr(ListmonkClient, "_request", fake_request)
    client = ListmonkClient.__new__(ListmonkClient)

    await client.get_recipient_estimate([4, 5], "optin")

    assert calls == [
        ("GET", "/api/campaigns/estimate", {"list_id": [4, 5], "type": "optin"})
    ]


NOW = server.datetime(2026, 11, 19, 12, 0, tzinfo=server.UTC)


@pytest.mark.parametrize(
    ("campaign", "expected"),
    [
        ({"daily_limit": 0}, "none"),
        ({"daily_limit": None}, "none"),
        (
            {"daily_limit": 500, "daily_sent": 0, "daily_window_start": None},
            "500/day (0 sent in the current 24-hour window)",
        ),
        (
            {
                "status": "running",
                "daily_limit": 500,
                "daily_sent": 120,
                "daily_window_start": "2026-11-19T06:00:00Z",
            },
            "500/day (120 sent in the current 24-hour window)",
        ),
        (
            # Go's RFC 3339 output with nanoseconds and a local offset.
            {
                "status": "running",
                "daily_limit": 500,
                "daily_sent": 500,
                "daily_window_start": "2026-11-19T11:45:00.123456789+05:45",
            },
            "500/day (500 sent in the current 24-hour window; limit reached, "
            "the next window opens at 2026-11-20 06:00 UTC)",
        ),
        (
            # The window has ended, so the stored count is from a past window.
            {
                "status": "paused",
                "daily_limit": 500,
                "daily_sent": 500,
                "daily_window_start": "2026-11-18T11:00:00Z",
            },
            "500/day (0 sent in the current 24-hour window)",
        ),
        (
            {
                "status": "finished",
                "daily_limit": 500,
                "daily_sent": 500,
                "daily_window_start": "2026-11-19T06:00:00Z",
            },
            "500/day (500 sent in the current 24-hour window)",
        ),
        (
            {"daily_limit": 500, "daily_sent": 9, "daily_window_start": "not a date"},
            "500/day (0 sent in the current 24-hour window)",
        ),
    ],
)
def test_daily_limit_summary(campaign: dict[str, object], expected: str) -> None:
    assert server._daily_limit_summary(campaign, now=NOW) == expected


@pytest.mark.asyncio
async def test_get_campaign_shows_daily_limit_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CampaignClient(FakeClient):
        async def get_campaign(self, campaign_id: int) -> dict[str, object]:
            return {
                "data": {
                    "id": campaign_id,
                    "name": "Daily",
                    "status": "draft",
                    "lists": [{"id": 7}],
                    "daily_limit": 250,
                    "daily_sent": 0,
                    "daily_window_start": None,
                    "body": "<p>x</p>",
                }
            }

    monkeypatch.setattr(server, "get_client", lambda: CampaignClient())

    result = await server.get_campaign(3)

    assert "Daily Limit: 250/day (0 sent in the current 24-hour window)" in result
