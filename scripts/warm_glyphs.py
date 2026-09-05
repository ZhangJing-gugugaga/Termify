#!/usr/bin/env python3
"""汉字活字引擎 · 离线预热脚本（站主手动运行，不进启动流程）。

为高频汉字预生成全部（或指定）风格的等宽字形并写入 SQLite 缓存
（data/cjk_glyphs.db），让线上首次请求直接命中缓存、零等待。

运行成本估算：500 字 × 3 风格 ≈ 1500 次 glm-4-flash 调用（每次仅数百
token），费用约几元人民币；Ollama 本地端点则零费用、只耗时间。

用法（项目根目录）：
    .venv/Scripts/python.exe scripts/warm_glyphs.py                # 全量
    .venv/Scripts/python.exe scripts/warm_glyphs.py --limit 100    # 前 100 字
    .venv/Scripts/python.exe scripts/warm_glyphs.py --style pixel  # 单风格
    .venv/Scripts/python.exe scripts/warm_glyphs.py --concurrency 4 --delay 0.5

断点续跑：已缓存（且 prompt_version 未变）的字自动跳过，可随时中断重跑。
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# 让脚本在任意工作目录都能 import termify（项目根入 sys.path）。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from termify import cjk_glyph, llm  # noqa: E402

# 《现代汉语常用字表》一级常用字（按使用频度排序）前 500 字，紧凑硬编码。
# 自检约定：len(set()) == 500 且全部落在 \u4e00-\u9fff（见 _self_check）。
HIGH_FREQ_CHARS = (
    "的一是了我不人在他有这个上们来到" "时大地为子中你说生国年着就那和要" "她出也得里后自以会家可下而过天去" "能对小多然于心学么之都好看起发当" "没成只如事把还用第样道想作种开美" "总从无情己面最女但现前些所同日手"
    "又行意动方期它头经长儿回位分爱老" "因很给名法间斯知世什两次使身者被" "高已亲其进此话常与活正感见明问力" "理尔点文几定本公特做外孩相西果走" "将月十实向声车全信重三机工物气每" "并别真打太新比才便夫再书部水像眼"
    "等体却加电主界门利海受听表德少克" "代员许先口由死安写性马光白或住难" "望教命花结乐色更拉东神记处让母父" "应直字场平报友关放至张认接告入笑" "内英军候民岁往何度山觉路带万男边" "风解叫任金快原吃妈变通师立象数四"
    "失满战远格士音轻目条呢病始达深完" "今提求清王化空业思切怎非找片罗钱" "吗语元喜曾离飞科言干流欢约各即指" "合反题必该论交终林请医晚制球决传" "画保读运及则房早院量苦火布品近坐" "产答星精视五连司巴奇管类未朋且婚"
    "台夜青北队久乎越观落尽形影红爸百" "令周吧识步希亚术留市半热送兴造谈" "容极随演收首根讲整式取照办强石古" "华拿计您装似足双妻尼转诉米称丽客" "南领节衣站黑刻统断福城故历惊脸选" "包紧争另建维绝树系伤示愿持千史谁"
    "准联妇纪基买志静阿诗独复痛消社算" "义竟确酒"
)


def _self_check() -> None:
    """硬编码字表自检：500 个不重复汉字，全部在基本区。"""
    chars = list(dict.fromkeys(HIGH_FREQ_CHARS))  # 去重且保持顺序
    assert len(chars) == 500, f"高频字表去重后 {len(chars)} 个，应为 500"
    bad = [c for c in chars if not ("\u4e00" <= c <= "\u9fff")]
    assert not bad, f"字表含非 CJK 字符: {bad}"


def _pick_styles(style_arg: str) -> list[dict]:
    if style_arg == "all":
        return list(cjk_glyph.GLYPH_STYLES)
    style = cjk_glyph.style_by_slug(style_arg)
    if style is None:
        raise SystemExit(f"未知风格: {style_arg}（可选 pixel/brush/outline/all）")
    return [style]


def main() -> int:
    _self_check()
    parser = argparse.ArgumentParser(description="高频汉字字形预热")
    parser.add_argument("--limit", type=int, default=500,
                        help="本次预热的字数（默认全部 500）")
    parser.add_argument("--style", default="all",
                        choices=["pixel", "brush", "outline", "all"],
                        help="预热风格（默认 all）")
    parser.add_argument("--concurrency", type=int, default=4,
                        help="并发线程数（默认 4）")
    parser.add_argument("--delay", type=float, default=0.5,
                        help="每次 LLM 调用之间的间隔秒数（默认 0.5）")
    args = parser.parse_args()

    cfg = llm.load_config(cjk_glyph.DEFAULT_DATA_DIR)
    if not llm.is_configured(cfg):
        print("LLM 未配置：请先在网页「自部署 AI」里配置，或手工编辑 "
              f"{cjk_glyph.db_path(cjk_glyph.DEFAULT_DATA_DIR)} 同目录的 "
              "llm_config.json。")
        return 1
    print(f"LLM: {cfg['model']} @ {cfg['base_url']}")

    styles = _pick_styles(args.style)
    chars = list(dict.fromkeys(HIGH_FREQ_CHARS))[:max(0, args.limit)]
    tasks = [(ch, s) for s in styles for ch in chars]

    # 断点续跑：先在主线程剔除已缓存任务。
    pending = []
    skipped = 0
    for ch, style in tasks:
        if cjk_glyph.is_cached(ch, style["slug"]):
            skipped += 1
        else:
            pending.append((ch, style))
    total = len(pending)
    print(f"任务：{len(chars)} 字 × {len(styles)} 风格 = {len(tasks)}；"
          f"已缓存跳过 {skipped}，待生成 {total}。")

    ok_count = 0
    fail: list[str] = []
    lock = threading.Lock()
    progress = {"n": 0}

    def worker(item: tuple[str, dict]) -> bool:
        ch, style = item
        # generate_glyph 内部已重试 3 次并吞异常；这里只记成败。
        rows = cjk_glyph.generate_glyph(ch, style, cfg, llm)
        time.sleep(args.delay)  # 线程内限速：降低对上游的瞬时压力
        if rows is None:
            return False
        cjk_glyph._cache_put(ch, style["slug"], rows,
                             cjk_glyph.db_path(cjk_glyph.DEFAULT_DATA_DIR))
        with lock:
            progress["n"] += 1
            if progress["n"] % 25 == 0 or progress["n"] == total:
                print(f"  进度 {progress['n']}/{total}")
        return True

    if total:
        with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
            futures = {pool.submit(worker, t): t for t in pending}
            for fut in as_completed(futures):
                ch, style = futures[fut]
                try:
                    if fut.result():
                        with lock:
                            ok_count += 1
                    else:
                        fail.append(f"{ch}({style['slug']})")
                except Exception as exc:  # noqa: BLE001 — 预热脚本不中断
                    fail.append(f"{ch}({style['slug']}): {exc}")

    print("─" * 48)
    print(f"完成：成功 {ok_count} / 失败 {len(fail)} / 已缓存跳过 {skipped}")
    if fail:
        print("失败清单（可重跑本脚本补齐）：")
        for item in fail:
            print("  " + item)
    print(f"缓存文件：{cjk_glyph.db_path(cjk_glyph.DEFAULT_DATA_DIR)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
