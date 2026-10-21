from __future__ import annotations

import json
from typing import Any, Optional

import httpx
import pytest
from pydantic import BaseModel

from hydrafetch import AsyncHydrafetch, Hydrafetch, HydrafetchError, HydrafetchTimeout


def make_client(handler, **kwargs: Any) -> Hydrafetch:
    kwargs.setdefault("max_retries", 0)
    client = Hydrafetch(api_key="hf_test", **kwargs)
    client._http = httpx.Client(
        base_url="https://api.hydrafetch.com",
        headers=client._http.headers,
        transport=httpx.MockTransport(handler),
    )
    return client


def _recording_client() -> tuple[Hydrafetch, list[dict[str, Any]]]:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"success": True, "data": {"results": []}})

    return make_client(handler), seen


def ok(body: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=body)


class Recorder:
    def __init__(self, body: Any, status: int = 200) -> None:
        self.body = body
        self.status = status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return ok(self.body, self.status)

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]

    def json_body(self) -> dict[str, Any]:
        return json.loads(self.last.content or b"{}")


class TestAuth:
    def test_sends_x_api_key_and_never_a_bearer_token(self) -> None:
        rec = Recorder({"success": True, "data": {}})
        make_client(rec).scrape("https://example.com")
        assert rec.last.headers["x-api-key"] == "hf_test"
        assert "authorization" not in rec.last.headers

    def test_raises_at_construction_when_no_key_is_available(self, monkeypatch) -> None:
        monkeypatch.delenv("HYDRAFETCH_API_KEY", raising=False)
        with pytest.raises(HydrafetchError) as exc:
            Hydrafetch()
        assert exc.value.code == "MISSING_API_KEY"

    def test_reads_the_key_from_the_environment(self, monkeypatch) -> None:
        monkeypatch.setenv("HYDRAFETCH_API_KEY", "hf_from_env")
        assert Hydrafetch()._api_key == "hf_from_env"


class TestRequestShaping:
    def test_unwraps_the_data_envelope(self) -> None:
        rec = Recorder({"success": True, "data": {"markdown": "# hi"}})
        assert make_client(rec).scrape("https://example.com")["markdown"] == "# hi"

    def test_options_ride_alongside_the_url(self) -> None:
        rec = Recorder({"success": True, "data": {}})
        make_client(rec).scrape("https://example.com", formats=["markdown"], preferStructure=True)
        assert rec.json_body() == {
            "url": "https://example.com",
            "formats": ["markdown"],
            "preferStructure": True,
        }

    def test_a_single_extract_url_becomes_a_list(self) -> None:
        rec = Recorder({"success": True, "data": {}})
        make_client(rec).extract("https://example.com", prompt="price")
        assert rec.json_body()["urls"] == ["https://example.com"]

    def test_none_valued_options_are_dropped_rather_than_sent_as_null(self) -> None:
        rec = Recorder({"success": True, "data": {}})
        make_client(rec).scrape("https://example.com", timeout=None, blockAds=True)
        assert rec.json_body() == {"url": "https://example.com", "blockAds": True}

    def test_brand_lookups_use_query_parameters_not_a_body(self) -> None:
        rec = Recorder({"success": True, "data": {}})
        make_client(rec).logo("stripe.com", theme="dark")
        assert rec.last.url.params["domain"] == "stripe.com"
        assert rec.last.url.params["theme"] == "dark"
        assert not rec.last.content

    def test_styleguide_reads_a_domain_or_one_page(self) -> None:
        rec = Recorder({"success": True, "data": {}})
        hf = make_client(rec)
        hf.styleguide("stripe.com")
        assert rec.last.url.params["domain"] == "stripe.com"
        hf.styleguide("https://stripe.com/pricing", maxAge=0)
        assert rec.last.url.params["url"] == "https://stripe.com/pricing"
        assert rec.last.url.params["maxAge"] == "0"
        assert "domain" not in rec.last.url.params


class TestErrors:
    def test_surfaces_code_status_and_request_id(self) -> None:
        rec = Recorder(
            {
                "success": False,
                "error": {"code": "UNAUTHORIZED", "message": "Invalid API key"},
                "meta": {"requestId": "abc"},
            },
            401,
        )
        with pytest.raises(HydrafetchError) as exc:
            make_client(rec).scrape("https://example.com")
        assert (exc.value.code, exc.value.status, exc.value.request_id) == (
            "UNAUTHORIZED",
            401,
            "abc",
        )

    @pytest.mark.parametrize(
        "status,flag",
        [
            (401, "is_auth"),
            (402, "is_out_of_credits"),
            (422, "is_invalid_request"),
            (429, "is_retryable"),
            (503, "is_retryable"),
        ],
    )
    def test_classifies_statuses_so_callers_need_not_memorise_them(self, status, flag) -> None:
        rec = Recorder({"success": False, "error": {"message": "x"}}, status)
        with pytest.raises(HydrafetchError) as exc:
            make_client(rec).scrape("https://example.com")
        assert getattr(exc.value, flag) is True

    def test_does_not_retry_a_validation_failure(self) -> None:
        rec = Recorder({"success": False, "error": {"message": "bad url"}}, 422)
        with pytest.raises(HydrafetchError):
            make_client(rec, max_retries=3).scrape("nope")
        assert len(rec.requests) == 1

    def test_retries_a_503_then_returns_the_success(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] < 3:
                return ok({"success": False, "error": {"message": "upstream"}}, 503)
            return ok({"success": True, "data": {"markdown": "ok"}})

        out = make_client(handler, max_retries=3).scrape("https://example.com")
        assert out["markdown"] == "ok"
        assert calls["n"] == 3


class TestJobs:
    def test_reads_batch_id_from_the_top_level_not_from_data(self) -> None:
        rec = Recorder({"batchId": "b_1", "status": "pending"}, 201)
        assert make_client(rec).start_batch(["https://a.example"]) == "b_1"

    def test_per_page_options_nest_under_scrape_options(self) -> None:
        rec = Recorder({"batchId": "b_1"}, 201)
        make_client(rec).start_batch(["https://a.example"], scrapeOptions={"formats": ["markdown"]})
        body = rec.json_body()
        assert body["scrapeOptions"] == {"formats": ["markdown"]}
        assert "formats" not in body

    def test_polls_until_terminal_and_reports_progress(self) -> None:
        statuses = iter(["pending", "running", "completed"])

        def handler(request: httpx.Request) -> httpx.Response:
            if "/batch/" in request.url.path:
                return ok({"success": True, "data": {"status": next(statuses, "completed")}})
            return ok({"batchId": "b_1"}, 201)

        seen: list[str] = []
        job = make_client(handler).batch(
            ["https://a.example"],
            poll_interval=0.001,
            on_progress=lambda j: seen.append(j["status"]),
        )
        assert job["status"] == "completed"
        assert seen == ["pending", "running", "completed"]

    def test_times_out_rather_than_polling_forever(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if "/batch/" in request.url.path:
                return ok({"success": True, "data": {"status": "running"}})
            return ok({"batchId": "b_1"}, 201)

        with pytest.raises(HydrafetchTimeout):
            make_client(handler).batch(
                ["https://a.example"], poll_interval=0.001, job_timeout=0.005
            )


class TestAsyncClient:
    async def test_mirrors_the_sync_surface(self) -> None:
        rec = Recorder({"success": True, "data": {"markdown": "# async"}})
        client = AsyncHydrafetch(api_key="hf_test", max_retries=0)
        client._http = httpx.AsyncClient(
            base_url="https://api.hydrafetch.com",
            headers=client._http.headers,
            transport=httpx.MockTransport(rec),
        )
        async with client:
            out = await client.scrape("https://example.com")
        assert out["markdown"] == "# async"
        assert rec.last.headers["x-api-key"] == "hf_test"


def test_extract_accepts_a_pydantic_model_and_inlines_nested_ones():
    class Address(BaseModel):
        city: str

    class Person(BaseModel):
        name: str
        home: Address

    client, seen = _recording_client()
    client.extract("https://example.com", schema=Person)

    schema = seen[0]["schema"]
    assert schema["properties"]["name"]["type"] == "string"
    assert schema["properties"]["home"]["properties"]["city"]["type"] == "string"
    assert "$defs" not in schema and "$ref" not in str(schema)


def test_extract_still_takes_plain_json_schema():
    client, seen = _recording_client()
    plain = {"type": "object", "properties": {"name": {"type": "string"}}}
    client.extract("https://example.com", schema=plain)
    assert seen[0]["schema"] == plain


class Node(BaseModel):
    child: Optional[Node] = None


def test_extract_rejects_a_recursive_model_rather_than_sending_a_ref():
    client, _ = _recording_client()
    with pytest.raises(HydrafetchError) as err:
        client.extract("https://example.com", schema=Node)
    assert err.value.code == "SCHEMA_CONVERSION_FAILED"


def test_rate_result_posts_the_rating_for_the_last_request() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1/web/feedback":
            return ok({"success": True, "data": {"id": "f1", "requestId": "r1", "rating": "bad"}})
        return httpx.Response(
            200, json={"success": True, "data": {}}, headers={"x-request-id": "r1"}
        )

    client = make_client(handler)
    client.scrape("https://a.com")
    assert client.last_request_id == "r1"

    out = client.rate_result(
        "bad", request_id=client.last_request_id, reasons=["missing_content"], note="no prices"
    )

    assert out == {"id": "f1", "requestId": "r1", "rating": "bad"}
    assert json.loads(seen[-1].content) == {
        "requestId": "r1",
        "rating": "bad",
        "reasons": ["missing_content"],
        "note": "no prices",
    }


def test_rate_result_can_rate_by_url_and_needs_a_target() -> None:
    rec = Recorder({"success": True, "data": {"id": "f2", "requestId": "r2", "rating": "good"}})
    client = make_client(rec)
    client.rate_result("good", url="https://a.com")
    assert json.loads(rec.last.content) == {"url": "https://a.com", "rating": "good"}
    with pytest.raises(HydrafetchError):
        client.rate_result("good")
