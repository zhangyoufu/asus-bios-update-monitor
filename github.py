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

github_headers = {
    'Accept': 'application/vnd.github.v3+json',
    'Authorization': f'token {GITHUB_TOKEN}',
}

GitHubReleaseDict: TypeAlias = dict[str, Any]
GitHubReleaseAssetDict: TypeAlias = dict[str, Any]

def github_release_ensure(tag_name: str, name: str, timestamp: datetime.datetime) -> GitHubReleaseDict:
    release = github_release_get_by_tag(tag_name)
    if not release:
        release = github_release_create(tag_name, name, timestamp)
    return release

def github_release_get_by_tag(tag_name: str) -> GitHubReleaseDict:
    rsp = requests.get(f'https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/tags/{tag_name}',
        headers=github_headers,
        allow_redirects=False,
    )
    if rsp.status_code == 200:
        return rsp.json()
    assert rsp.status_code == 404, f'HTTP {rsp.status_code} {rsp.reason}\n{rsp.text}'

def github_release_patch(release: GitHubReleaseDict, **kwargs) -> None:
    rsp = requests.patch(release['url'], headers=github_headers, allow_redirects=False, json=kwargs)
    assert rsp.status_code == 200, f'HTTP {rsp.status_code} {rsp.reason}\n{rsp.text}'

def github_release_create(tag_name: str, name: str, timestamp: datetime.datetime) -> GitHubReleaseDict:
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
    rsp = requests.post(f'https://api.github.com/repos/{GITHUB_REPOSITORY}/releases',
        headers=github_headers,
        json={
            'tag_name': tag_name,
            'name': name,
        },
        allow_redirects=False,
    )
    assert rsp.status_code == 201, f'HTTP {rsp.status_code} {rsp.reason}\n{rsp.text}'
    return rsp.json()

def github_release_get_assets(assets_url: str) -> list[GitHubReleaseAssetDict]:
    assets = []
    url = f'{assets_url}?per_page=100'
    while 1:
        rsp = requests.get(url=url, headers=github_headers, allow_redirects=False)
        assert rsp.status_code == 200, f'HTTP {rsp.status_code} {rsp.reason}\n{rsp.text}'
        assets.extend(rsp.json())
        if 'next' not in rsp.links:
            break
        url = rsp.links['next']['url']
    return assets

def github_release_delete_asset(assets_url: str, name: str) -> None:
    for asset in github_release_get_assets(assets_url):
        if asset['name'] == name:
            break
    else:
        raise ValueError(f'asset {name} not found')
    rsp = requests.delete(url=asset['url'], headers=github_headers, allow_redirects=False)
    assert rsp.status_code == 204, f'HTTP {rsp.status_code} {rsp.reason}\n{rsp.text}'

def github_release_upload_asset(release: GitHubReleaseDict, filename: str, src: io.RawIOBase) -> None:
    logger.info(f'Uploading {filename}...')
    upload_url = release['upload_url'].split('{', 1)[0]
    for retry in range(3):
        src.seek(0)
        try:
            rsp = requests.post(upload_url,
                params={'name': filename},
                headers=github_headers | {
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
                    github_release_delete_asset(release['assets_url'], filename)
                finally:
                    pass
            else:
                assert rsp.status_code in [500, 502, 504], f'HTTP {rsp.status_code} {rsp.reason}\n{rsp.text}'
        except requests.ConnectionError:
            logger.exception('Connection error')
            ## somehow, existed assets may also cause ConnectionError
            try:
                github_release_delete_asset(release['assets_url'], filename)
            finally:
                pass
        logger.error('Upload failed, retry')
    else:
        logger.critical('Upload aborted')
        raise RuntimeError('Upload aborted')
