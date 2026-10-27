"""lint 规则单元测试:python tests/test_lint.py"""
import sys

sys.path.insert(0, ".")

from app.lint import lint_text


def main():
    bad = (
        "空气中弥漫着紧张的气息。他眸中闪过一丝惊讶,眸中闪过一丝慌乱,嘴角勾起一抹弧度,"
        "嘴角勾起一抹冷笑,握紧了拳头,又握紧了拳头。"
        "这不是愤怒而是一种深深的无力感,是一种深深的挫败感。值得注意的是,雨下得像眼泪,又像委屈。"
        "夜色像墨,又像一床盖不住的旧棉被。"
        "这不是告别而是开始。不是结束而是重生。不是偶然而是必然。"
        "他说——她答——风停了——夜深了——就这样——结束了——一切——归于平静。"
    )
    issues = lint_text(bad)
    ids = {i["rule"] for i in issues}
    expected = {"cliche_air", "template_eye", "template_mouth", "template_fist",
                "scaffold_correlation", "scaffold_summary", "scaffold_double_simile",
                "scaffold_emotion_label", "dash_overuse"}
    missing = expected - ids
    assert not missing, f"漏检: {missing}"
    assert all(i["suggestion"] for i in issues), "缺少改写方向"

    clean = "他把碗放下,数了三枚铜钱,推回给她。雨还在下,但檐下是干的。"
    assert lint_text(clean) == [], "干净文本误报"
    print("lint tests passed ✓")


if __name__ == "__main__":
    main()
