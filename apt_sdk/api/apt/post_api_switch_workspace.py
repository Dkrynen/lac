from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.post_api_switch_workspace_body import PostApiSwitchWorkspaceBody
from ...models.post_api_switch_workspace_response_200 import (
    PostApiSwitchWorkspaceResponse200,
)
from ...types import UNSET, Response, Unset


def _get_kwargs(
    workspace_id: str,
    *,
    body: PostApiSwitchWorkspaceBody | Unset = UNSET,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/workspaces/{workspace_id}/switch".format(
            workspace_id=quote(str(workspace_id), safe=""),
        ),
    }

    if not isinstance(body, Unset):
        _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> PostApiSwitchWorkspaceResponse200 | None:
    if response.status_code == 200:
        response_200 = PostApiSwitchWorkspaceResponse200.from_dict(response.json())

        return response_200

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[PostApiSwitchWorkspaceResponse200]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    workspace_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: PostApiSwitchWorkspaceBody | Unset = UNSET,
) -> Response[PostApiSwitchWorkspaceResponse200]:
    """Api switch workspace

    Args:
        workspace_id (str):
        body (PostApiSwitchWorkspaceBody | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[PostApiSwitchWorkspaceResponse200]
    """

    kwargs = _get_kwargs(
        workspace_id=workspace_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    workspace_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: PostApiSwitchWorkspaceBody | Unset = UNSET,
) -> PostApiSwitchWorkspaceResponse200 | None:
    """Api switch workspace

    Args:
        workspace_id (str):
        body (PostApiSwitchWorkspaceBody | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        PostApiSwitchWorkspaceResponse200
    """

    return sync_detailed(
        workspace_id=workspace_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    workspace_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: PostApiSwitchWorkspaceBody | Unset = UNSET,
) -> Response[PostApiSwitchWorkspaceResponse200]:
    """Api switch workspace

    Args:
        workspace_id (str):
        body (PostApiSwitchWorkspaceBody | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[PostApiSwitchWorkspaceResponse200]
    """

    kwargs = _get_kwargs(
        workspace_id=workspace_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    workspace_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: PostApiSwitchWorkspaceBody | Unset = UNSET,
) -> PostApiSwitchWorkspaceResponse200 | None:
    """Api switch workspace

    Args:
        workspace_id (str):
        body (PostApiSwitchWorkspaceBody | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        PostApiSwitchWorkspaceResponse200
    """

    return (
        await asyncio_detailed(
            workspace_id=workspace_id,
            client=client,
            body=body,
        )
    ).parsed
