"""把页面用到的字体子集化后内嵌进 index.html。

背景：页面原来从 Google Fonts 的 CDN 取字体，那套 CDN 国内取不到，页面会静默
回退到系统字体（微软雅黑 / 宋体），设计里"衬线标题 + 等宽数字"的部分全丢了。
这个脚本把页面实际用到的字符抽出来做成子集，base64 内嵌进 HTML，页面就完全
自包含——不依赖任何外部请求，离线也能正确显示。

改了页面文字之后要重新跑一次，否则新出现的字会掉回系统字体：

    python _tool/embed_fonts.py

字体源用 google/fonts 仓库的原始文件。Google Fonts 的 CSS 接口对老 UA 返回的
是内部格式（不是可用的 TTF），验证过不可用，别改回去。中文字体是可变字体，
先按字重实例化再子集化。

脚本幂等：重跑会摘掉上次嵌入的字体块再重新生成。下载的文件缓存在 _tool/_work/，
重复运行不会重新下载。
"""

import base64
import os
import re
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "index.html")
WORK = os.path.join(ROOT, "_tool", "_work")

RAW = "https://raw.githubusercontent.com/google/fonts/main/ofl/"

# 家族 -> (可变字体地址, 需要的字重)
VARIABLE = {
    "Noto Serif SC": (RAW + "notoserifsc/NotoSerifSC%5Bwght%5D.ttf", [600, 700]),
    "Noto Sans SC": (RAW + "notosanssc/NotoSansSC%5Bwght%5D.ttf", [400, 500, 700]),
}
# (家族, 字重) -> 静态字体地址
STATIC = {
    ("IBM Plex Mono", 400): RAW + "ibmplexmono/IBMPlexMono-Regular.ttf",
    ("IBM Plex Mono", 500): RAW + "ibmplexmono/IBMPlexMono-Medium.ttf",
}

START = "/* >>> embedded fonts >>> */"
END = "/* <<< embedded fonts <<< */"

SIGNATURES = (b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf", b"wOFF")


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=600).read()


def download(url, dest):
    if os.path.exists(dest) and open(dest, "rb").read(4) in SIGNATURES:
        print(f"    缓存命中 {os.path.basename(dest)}")
        return dest
    print(f"    下载 {os.path.basename(dest)} ...")
    data = fetch(url)
    if data[:4] not in SIGNATURES:
        raise RuntimeError(f"{url} 不是字体文件（头 {data[:4]!r}，{len(data)} 字节）")
    open(dest, "wb").write(data)
    print(f"      {len(data) // 1024 // 1024} MB")
    return dest


def run(*args):
    subprocess.run([sys.executable, "-m", *args], check=True)


def main():
    html = open(SRC, encoding="utf-8").read()
    before_kb = len(html.encode("utf-8")) // 1024

    html = re.sub(re.escape(START) + r".*?" + re.escape(END), "", html, flags=re.S)
    html = re.sub(r"\n<link[^>]*fonts\.(?:googleapis|gstatic)\.com[^>]*>", "", html)

    charset = "".join(sorted(set(html)))
    cjk = sum(1 for c in charset if "一" <= c <= "鿿")
    print(f"字符集 {len(charset)} 个（汉字 {cjk} 个）")

    os.makedirs(WORK, exist_ok=True)
    charfile = os.path.join(WORK, "chars.txt")
    open(charfile, "w", encoding="utf-8").write(charset)

    jobs = []
    for fam, (url, weights) in VARIABLE.items():
        print(f"  {fam}")
        vf = download(url, os.path.join(WORK, fam.replace(" ", "") + "-VF.ttf"))
        for wt in weights:
            static = os.path.join(WORK, f"{fam.replace(' ', '')}-{wt}.ttf")
            if not os.path.exists(static):
                run("fontTools.varLib.instancer", vf, f"wght={wt}", "-o", static)
            jobs.append((fam, wt, static))
    for (fam, wt), url in STATIC.items():
        print(f"  {fam} {wt}")
        jobs.append((fam, wt, download(url, os.path.join(WORK, f"{fam.replace(' ', '')}-{wt}.ttf"))))

    faces, total = [], 0
    for fam, wt, src in jobs:
        woff2 = os.path.join(WORK, f"{fam.replace(' ', '')}-{wt}.subset.woff2")
        run(
            "fontTools.subset", src,
            f"--text-file={charfile}",
            "--flavor=woff2",
            "--layout-features=*",
            "--no-hinting",
            f"--output-file={woff2}",
        )
        b64 = base64.b64encode(open(woff2, "rb").read()).decode()
        total += len(b64)
        print(f"  {fam} {wt}: {len(b64) // 1024} KB")
        faces.append(
            f"@font-face{{font-family:'{fam}';font-style:normal;font-weight:{wt};"
            f"font-display:swap;"
            f"src:url(data:font/woff2;base64,{b64}) format('woff2')}}"
        )

    html = html.replace(
        "</style>", START + "\n" + "\n".join(faces) + "\n" + END + "\n</style>", 1
    )
    open(SRC, "w", encoding="utf-8").write(html)

    now_kb = os.path.getsize(SRC) // 1024
    print(f"字体合计 {total // 1024} KB，index.html {before_kb} KB -> {now_kb} KB")


if __name__ == "__main__":
    main()
