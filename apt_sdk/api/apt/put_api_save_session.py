from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.put_api_save_session_body import PutApiSaveSessionBody
from ...models.put_api_save_session_response_200 import PutApiSaveSessionResponse200
from ...types import UNSET, Response, Unset


def _get_kwargs(
    session_id: str,
    *,
    body: PutApiSaveSessionBody | Unset = UNSET,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "put",
        "url": "/api/sessions/{session_id}".format(
            session_id=quote(str(session_id), safe=""),
        ),
    }

    if not isinstance(body, Unset):
        _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> PutApiSaveSessionResponse200 | None:
    if response.status_code == 200:
        response_200 = PutApiSaveSessionResponse200.from_dict(response.json())

        return response_200

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[PutApiSaveSessionResponse200]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    session_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: PutApiSaveSessionBody | Unset = UNSET,
) -> Response[PutApiSaveSessionResponse200]:
    """Api save session

    Args:
        session_id (str):
        body (PutApiSaveSessionBody | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[PutApiSaveSessionResponse200]
    """

    kwargs = _get_kwargs(
        session_id=session_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    session_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: PutApiSaveSessionBody | Unset = UNSET,
) -> PutApiSaveSessionResponse200 | None:
    """Api save session

    Args:
        session_id (str):
        body (PutApiSaveSessionBody | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        PutApiSaveSessionResponse200
    """

    return sync_detailed(
        session_id=session_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    session_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: PutApiSaveSessionBody | Unset = UNSET,
) -> Response[PutApiSaveSessionResponse200]:
    """Api save session

    Args:
        session_id (str):
        body (PutApiSaveSessionBody | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[PutApiSaveSessionResponse200]
    """

    kwargs = _get_kwargs(
        session_id=session_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    session_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: PutApiSaveSessionBody | Unset = UNSET,
) -> PutApiSaveSessionResponse200 | None:
    """Api save session

    Args:
        session_id (str):
        body (PutApiSaveSessionBody | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        PutApiSaveSessionResponse200
    """

    return (
        await asyncio_detailed(
            session_id=session_id,
            client=client,
            body=body,
        )
    ).parsed
