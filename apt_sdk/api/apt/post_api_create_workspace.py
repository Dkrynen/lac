from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.post_api_create_workspace_body import PostApiCreateWorkspaceBody
from ...models.post_api_create_workspace_response_200 import (
    PostApiCreateWorkspaceResponse200,
)
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    body: PostApiCreateWorkspaceBody | Unset = UNSET,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/workspaces",
    }

    if not isinstance(body, Unset):
        _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> PostApiCreateWorkspaceResponse200 | None:
    if response.status_code == 200:
        response_200 = PostApiCreateWorkspaceResponse200.from_dict(response.json())

        return response_200

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[PostApiCreateWorkspaceResponse200]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: PostApiCreateWorkspaceBody | Unset = UNSET,
) -> Response[PostApiCreateWorkspaceResponse200]:
    """Api create workspace

    Args:
        body (PostApiCreateWorkspaceBody | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[PostApiCreateWorkspaceResponse200]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    body: PostApiCreateWorkspaceBody | Unset = UNSET,
) -> PostApiCreateWorkspaceResponse200 | None:
    """Api create workspace

    Args:
        body (PostApiCreateWorkspaceBody | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        PostApiCreateWorkspaceResponse200
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: PostApiCreateWorkspaceBody | Unset = UNSET,
) -> Response[PostApiCreateWorkspaceResponse200]:
    """Api create workspace

    Args:
        body (PostApiCreateWorkspaceBody | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[PostApiCreateWorkspaceResponse200]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: PostApiCreateWorkspaceBody | Unset = UNSET,
) -> PostApiCreateWorkspaceResponse200 | None:
    """Api create workspace

    Args:
        body (PostApiCreateWorkspaceBody | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        PostApiCreateWorkspaceResponse200
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
