#!/usr/bin/env python3
from typing import Any, TypeAlias
import datetime
import io
import logging
import os
import requests
import subprocess

logger = logging.getLogger(__name__)

GITHUB_REPOSITORY = os.environ['GITHUB_REPOSITORY']
GITHUB_TOKEN = os.environ['GITHUB_TOKEN']

session = requests.Session()
session.cookies._policy.set_ok = lambda cookie, request: False
session.headers = {
    'Accept': 'application/vnd.github.v3+json',
    'Authorization': f'token {GITHUB_TOKEN}',
}

ReleaseDict: TypeAlias = dict[str, Any]
ReleaseAssetDict: TypeAlias = dict[str, Any]

def list_release() -> list[ReleaseDict]:
    releases = []
    url = f'https://api.github.com/repos/{GITHUB_REPOSITORY}/releases?per_page=100'
    while 1:
        rsp = session.get(
            url=url,
            allow_redirects=False,
        )
        assert rsp.status_code == 200, f'HTTP {rsp.status_code} {rsp.reason}\n{rsp.text}'
        releases.extend(rsp.json())
        if 'next' not in rsp.links:
            break
        url = rsp.links['next']['url']
    return releases

def ensure_release(tag_name: str, name: str, timestamp: datetime.datetime) -> ReleaseDict:
    release = get_release_by_tag(tag_name)
    if not release:
        release = create_release(tag_name, name, timestamp)
    return release

def get_release_by_tag(tag_name: str) -> ReleaseDict:
    rsp = session.get(f'https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/tags/{tag_name}',
        allow_redirects=False,
    )
    if rsp.status_code == 200:
        return rsp.json()
    assert rsp.status_code == 404, f'HTTP {rsp.status_code} {rsp.reason}\n{rsp.text}'

def patch_release(release: ReleaseDict, **kwargs) -> None:
    rsp = session.patch(release['url'], allow_redirects=False, json=kwargs)
    assert rsp.status_code == 200, f'HTTP {rsp.status_code} {rsp.reason}\n{rsp.text}'

def create_release(tag_name: str, name: str, timestamp: datetime.datetime) -> ReleaseDict:
    subprocess.run(f'''set -eux
COMMIT=$(git commit-tree 4b825dc642cb6eb9a060e54bf8d69288fbee4904 </dev/null)
git tag -f {tag_name} $COMMIT
git push -f -u origin refs/tags/{tag_name}
''',
        env=os.environ | dict(
            GIT_AUTHOR_NAME='Bot',
            GIT_AUTHOR_EMAIL='bot@example.com',
            GIT_AUTHOR_DATE=timestamp.isoformat()+'Z',
            GIT_COMMITTER_NAME='Bot',
            GIT_COMMITTER_EMAIL='bot@example.com',
            GIT_COMMITTER_DATE=timestamp.isoformat()+'Z',
        ),
        shell=True,
        check=True,
    )
    rsp = session.post(f'https://api.github.com/repos/{GITHUB_REPOSITORY}/releases',
        json={
            'tag_name': tag_name,
            'name': name,
        },
        allow_redirects=False,
    )
    assert rsp.status_code == 201, f'HTTP {rsp.status_code} {rsp.reason}\n{rsp.text}'
    return rsp.json()

def get_release_assets(assets_url: str) -> list[ReleaseAssetDict]:
    assets = []
    url = f'{assets_url}?per_page=100'
    while 1:
        rsp = session.get(url=url, allow_redirects=False)
        assert rsp.status_code == 200, f'HTTP {rsp.status_code} {rsp.reason}\n{rsp.text}'
        assets.extend(rsp.json())
        if 'next' not in rsp.links:
            break
        url = rsp.links['next']['url']
    return assets

def delete_release_asset(assets_url: str, name: str) -> None:
    for asset in get_release_assets(assets_url):
        if asset['name'] == name:
            break
    else:
        raise ValueError(f'asset {name} not found')
    rsp = session.delete(url=asset['url'], allow_redirects=False)
    assert rsp.status_code == 204, f'HTTP {rsp.status_code} {rsp.reason}\n{rsp.text}'

def upload_release_asset(release: ReleaseDict, filename: str, src: io.RawIOBase) -> None:
    logger.info(f'Uploading {filename}...')
    upload_url = release['upload_url'].split('{', 1)[0]
    for retry in range(3):
        src.seek(0)
        try:
            rsp = session.post(upload_url,
                params={'name': filename},
                headers={
                    'Content-Type': 'application/octet-stream',
                },
                data=src,
                allow_redirects=False,
            )
            if rsp.status_code == 201:
                break
            logger.error(f'HTTP {rsp.status_code} {rsp.reason}\n{rsp.text}')
            if rsp.status_code == 422:
                ## assume {"resource":"ReleaseAsset","code":"already_exists","field":"name"}
                try:
                    delete_release_asset(release['assets_url'], filename)
                finally:
                    pass
            else:
                assert rsp.status_code in [500, 502, 504], f'HTTP {rsp.status_code} {rsp.reason}\n{rsp.text}'
        except requests.ConnectionError:
            logger.exception('Connection error')
            ## somehow, existed assets may also cause ConnectionError
            try:
                delete_release_asset(release['assets_url'], filename)
            finally:
                pass
        logger.error('Upload failed, retry')
    else:
        logger.critical('Upload aborted')
        raise RuntimeError('Upload aborted')
