#!/usr/bin/env python3
import dataclasses
import datetime
import github
import logging
import re
import requests
import tempfile
import zoneinfo


logger = logging.getLogger(__name__)


@dataclasses.dataclass
class BIOSRelease:
    date: datetime.date
    version: str
    title: str
    url: str
    description: str


def fetch() -> list[BIOSRelease]:
    url = 'https://www.asus.com/support/api/product.asmx/GetPDBIOS?website=global&pdid=21002'
    rsp = requests.get(url, headers={'User-Agent': 'Mozilla'})
    assert rsp.status_code == 200, f'HTTP {rsp.status_code} {rsp.reason}\n{rsp.text}'
    body = rsp.json()
    assert body['Status'] == 'SUCCESS', rsp.text
    obj = body['Result']['Obj'][0]
    assert obj['Name'] == 'BIOS', rsp.text
    result = []
    for bios_file in obj['Files']:
        description = bios_file['Description'].strip('"').replace('<br/>', '\n')
        description = re.sub(r'\n*Before running the USB BIOS Flashback tool, please rename the BIOS file ?\(PX670ERW\.CAP\) using BIOSRenamer\.\n*', '', description)
        result.append(BIOSRelease(
            date=datetime.date.fromisoformat(bios_file['ReleaseDate'].replace('/', '-')),
            version=bios_file['Version'],
            title=bios_file['Title'],
            url=bios_file['DownloadUrl']['Global'].split('?', 1)[0],
            description=description,
        ))
    return result


def process(bios: BIOSRelease) -> None:
    release = github.ensure_release(
        tag_name=bios.title.replace(' ', '_'),
        name=bios.title,
        timestamp=datetime.datetime.combine(bios.date, datetime.time(), tzinfo=zoneinfo.ZoneInfo('Asia/Shanghai')),
    )
    github.patch_release(release, body=bios.description)
    with tempfile.TemporaryFile() as f:
        rsp = requests.get(
            url=bios.url,
            allow_redirects=False,
            stream=True,
        )
        assert rsp.status_code == 200, f'HTTP {rsp.status_code} {rsp.reason}\n{rsp.text}'
        for chunk in rsp.iter_content(16*1024*1024):
            f.write(chunk)
        github.upload_release_asset(release, bios.url.rsplit('/', 1)[-1], f)


def main() -> None:
    logging.basicConfig(level=logging.DEBUG)
    logging.getLogger('urllib3').setLevel(logging.INFO)
    known = set(release['name'] for release in github.list_release())
    for bios in fetch():
        if re.fullmatch(r'\d+', bios.version) and bios.title == '':
            bios.title = f'PRIME X670E-PRO WIFI BIOS {bios.version}'
        assert bios.title.strip(), bios
        if bios.title in known:
            continue
        logger.info('processing %s', bios.title)
        process(bios)


if __name__ == '__main__':
    main()
