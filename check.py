#!/usr/bin/env python3
from typing import Any, TypeAlias
import ast
import dataclasses
import datetime
import github
import logging
import pathlib
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
    # url = 'https://www.asus.com.cn/motherboards-components/motherboards/prime/prime-x670e-pro-wifi/helpdesk_bios?model2Name=PRIME-X670E-PRO-WIFI'
    url = 'https://www.asus.com/motherboards-components/motherboards/prime/prime-x670e-pro-wifi/helpdesk_bios?model2Name=PRIME-X670E-PRO-WIFI'
    text = requests.get(url, headers={'User-Agent': 'Mozilla'}).text
    text = re.search(r'(?s)productSupportBIOS:\[\{Name:"BIOS",Count:.*?,Files:\[(.*?)\],IsDescShow:', text).group(1)
    logger.debug('web: %s', text)
    text = re.sub(r',(?:FileSize|IsRelease|PosType|HardwareInfoList|INFDate|SWID|ExeModule|Reboot|Ac_power|Usefor|Severity|UserSession|Sign|Tid|assistant_key):[A-Za-z]{1,2}(?=[,}])', '', text)
    text = re.sub(r'Id:".*?",', '', text)
    result = []
    while text:
        m = re.match(r'\{Version:("[^"]+"),Title:("[^"]+"),Description:(".*?"),(?:FileSize:"[^"]+",)?ReleaseDate:("[^"]+"),DownloadUrl:\{Global:("[^"]+"),China:(?:"[^"]+"|[A-Za-z]+)\}\}(?:,|$)', text)
        text = text[m.end():]
        version, title, description, release_date, url = map(ast.literal_eval, m.groups())
        description = description.strip('"').replace('<br/>', '\n')
        description = re.sub(r'\n*Before running the USB BIOS Flashback tool, please rename the BIOS file ?\(PX670ERW\.CAP\) using BIOSRenamer\.\n*', '', description)
        url = url.split('?', 1)[0]
        result.append(BIOSRelease(
            date=datetime.date.fromisoformat(release_date.replace('/', '-')),
            version=version,
            title=title,
            url=url,
            description=description,
        ))
    return result


def process(bios: BIOSRelease) -> None:
    release = github.github_release_ensure(
        tag_name=bios.title.replace(' ', '_'),
        name=bios.title,
        timestamp=datetime.datetime.combine(bios.date, datetime.time(), tzinfo=zoneinfo.ZoneInfo('Asia/Shanghai')),
    )
    github.github_release_patch(release, body=bios.description)
    with tempfile.TemporaryFile() as f:
        rsp = requests.get(
            url=bios.url,
            allow_redirects=False,
            stream=True,
        )
        assert rsp.status_code == 200, f'HTTP {rsp.status_code} {rsp.reason}\n{rsp.text}'
        for chunk in rsp.iter_content(16*1024*1024):
            f.write(chunk)
        github.github_release_upload_asset(release, bios.url.rsplit('/', 1)[-1], f)


state_file = pathlib.Path('state.txt')

def load_state() -> set[str]:
    if state_file.exists():
        return set(state_file.read_text().rstrip('\n').split('\n'))
    else:
        return set()

def save_state(state: set[str]) -> None:
    state_file.write_text(''.join(item+'\n' for item in sorted(state)))

def main() -> None:
    logging.basicConfig(level=logging.DEBUG)
    logging.getLogger('urllib3').setLevel(logging.INFO)
    state = load_state()
    for bios in fetch():
        if bios.title in state:
            continue
        logger.info('processing %s', bios.title)
        process(bios)
        state.add(bios.title)
        save_state(state)

if __name__ == '__main__':
    main()
