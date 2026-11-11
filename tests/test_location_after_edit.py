# -*- coding: utf-8 -*-
"""
编辑后重验证的定位测试（line / column / fragment）。

背景：
    配置编辑器通常先 ``del`` 一段字段，再修改另一段并重新验证。删除会改变
    文档布局，因此错误提示里的行列号、``start_line`` / ``end_line`` /
    ``lines()`` 到底锚定“编辑后的当前文件”还是“编辑前的原输入”，必须用
    实际输出的 YAML 逐行核对清楚。

标记约定：
    * 【当前文件坐标】——坐标可直接用于解析 ``yaml_object.as_yaml()``。
    * 【仅指原输入】——坐标只对编辑前/冻结的副本有意义，套用到当前文件会
      指向别的片段甚至越界，不能当成当前文件位置。这类用例同时把当前文件
      中同一行号真正的片段断言出来，防止误读。

这里刻画的是 strictyaml 当前（含其内置 ruamel）的真实行为；其中序列删除
后的陈旧坐标属于已知局限，用例将其固定下来，而不是假定它是正确行为。
"""

import pytest

from strictyaml import Bool, Int, Map, Optional, Seq, Str, YAMLValidationError, load
from strictyaml.ruamel import RoundTripDumper, dump


def _lines(yaml_text):
    """把 YAML 文本切成不带换行的行列表。"""
    return yaml_text.rstrip("\n").split("\n")


def _fragment(yaml_text, line_number):
    """取 YAML 文本中 1 基行号对应的片段；越界时给出明确占位。"""
    lines = _lines(yaml_text)
    if not 0 < line_number <= len(lines):
        return "<越界：当前文件共 {0} 行>".format(len(lines))
    return lines[line_number - 1]


# 含中文注释、块字符串、嵌套结构的同一份文档，供多个用例复用。
ORIGINAL_DOCUMENT = """\
# 顶层中文注释
name: my-service
# 块字符串字段说明
description: |
  第一行
  第二行
ports:
  - 8080
  - 8443
replicas: 3
# 尾部中文注释
"""

MAPPING_SCHEMA = Map(
    {
        "name": Str(),
        Optional("description"): Str(),
        "ports": Seq(Int()),
        "replicas": Int(),
    }
)


def test_mapping_delete_keeps_current_file_coordinates():
    """【当前文件坐标】删除映射字段后，存活键的坐标按删除后的文档重算。"""
    parsed = load(ORIGINAL_DOCUMENT, MAPPING_SCHEMA)
    del parsed["description"]

    current_yaml = parsed.as_yaml()
    # 期望的当前文件布局（实际输出即如此）：
    #   1 # 顶层中文注释
    #   2 name: my-service
    #   3 # 块字符串字段说明
    #   4 ports:
    #   5 - 8080
    #   6 - 8443
    #   7 replicas: 3
    #   8 # 尾部中文注释
    assert _lines(current_yaml) == [
        "# 顶层中文注释",
        "name: my-service",
        "# 块字符串字段说明",
        "ports:",
        "- 8080",
        "- 8443",
        "replicas: 3",
        "# 尾部中文注释",
    ]

    # 含前置中文注释：start_line 把注释行算进去。
    assert parsed["name"].start_line == 1
    assert parsed["name"].end_line == 3
    assert _fragment(current_yaml, parsed["name"].start_line) == "# 顶层中文注释"

    # ports 整体（含两个列表项）落在第 4-6 行。
    assert parsed["ports"].start_line == 4
    assert parsed["ports"].end_line == 6
    assert _fragment(current_yaml, parsed["ports"].start_line) == "ports:"
    assert _fragment(current_yaml, parsed["ports"].end_line) == "- 8443"

    # replicas 上移到第 7 行，并把尾部注释算作 end_line。
    assert parsed["replicas"].start_line == 7
    assert parsed["replicas"].end_line == 8
    assert _fragment(current_yaml, parsed["replicas"].start_line) == "replicas: 3"
    assert _fragment(current_yaml, parsed["replicas"].end_line) == "# 尾部中文注释"


def test_block_scalar_edit_then_locate():
    """【当前文件坐标】先删一段，再用多行 Python 值替换成块字符串后的定位。"""
    parsed = load(
        "# 中文标题\n"
        "# 第二行中文注释\n"
        "old: 将被删除\n"
        "note: 旧值\n"
        "count: 1\n",
        Map({Optional("old"): Str(), "note": Str(), "count": Int()}),
    )
    del parsed["old"]
    # 含换行的普通字符串经 to_yaml 变成保留样式的块标量（PreservedScalarString）。
    parsed["note"] = "换新\n块字符串"

    current_yaml = parsed.as_yaml()
    assert _lines(current_yaml) == [
        "# 中文标题",
        "# 第二行中文注释",
        "note: |-",
        "  换新",
        "  块字符串",
        "count: 1",
    ]

    note = parsed["note"]
    assert note.start_line == 1
    assert note.end_line == 5
    assert note.lines() == (
        "# 中文标题\n# 第二行中文注释\nnote: |-\n  换新\n  块字符串"
    )
    assert _fragment(current_yaml, note.start_line) == "# 中文标题"
    assert _fragment(current_yaml, note.end_line) == "  块字符串"


def test_revalidation_error_after_delete_points_at_edited_document():
    """
    【当前文件坐标（行号）】删字段后再赋非法 YAML 值，重验证错误的行列号
    锚定删除后的文档，而不是 11 行的原输入。

    注意：异常 mark 内嵌的 buffer 是一份“尝试赋值后”的 fork（含
    ``replicas: abc``），当前文件并未被写入，仍是 ``replicas: 3``。因此
    行号可用于当前文件，但高亮片段本身只存在于异常携带的 fork 里。
    """
    parsed = load(ORIGINAL_DOCUMENT, MAPPING_SCHEMA)
    del parsed["description"]

    current_yaml = parsed.as_yaml()
    assert len(_lines(current_yaml)) == 8  # 原输入 11 行，删除后 8 行

    with pytest.raises(YAMLValidationError) as exc_info:
        parsed["replicas"] = load("abc")

    error = exc_info.value
    context_mark = error.context_mark
    problem_mark = error.problem_mark

    # 行列号锚定 8 行的编辑后文档：context 在第 7 行、problem 在第 8 行。
    assert (context_mark.line + 1, context_mark.column + 1) == (7, 1)
    assert (problem_mark.line + 1, problem_mark.column + 1) == (8, 1)

    # 第 8 行在“当前文件”和“异常 fork”里都是同一条尾部注释，行号可直接套用。
    assert _fragment(current_yaml, problem_mark.line + 1) == "# 尾部中文注释"
    assert _fragment(problem_mark.buffer, problem_mark.line + 1) == "# 尾部中文注释"

    # 但出错片段只存在于异常 fork：fork 里是尝试值 abc，当前文件仍是原值 3。
    assert "replicas: abc" in problem_mark.buffer
    assert "replicas: 3" in current_yaml
    assert "replicas: abc" not in current_yaml
    # 两个 mark 共享同一份 fork 文档。
    assert context_mark.buffer == problem_mark.buffer


def test_flat_sequence_delete_leaves_stale_original_input_coordinates():
    """
    【仅指原输入】删除扁平序列元素后，存活元素的坐标不会随当前文件重算。

    实际当前文件里 ``乙`` 在第 3 行、``丙`` 在第 5 行；但报告的坐标仍按
    删除前的原输入布局给出，套到当前文件会指到错误片段（旧邻居 ``丙``、
    甚至下一个键 ``tail``）。
    """
    parsed = load(
        "# 中文注释\n"
        "items:\n"
        "  - 甲\n"
        "  - 乙\n"
        "  # 中间中文注释\n"
        "  - 丙\n"
        "tail: x\n",
        Map({"items": Seq(Str()), "tail": Str()}),
    )
    del parsed["items"][0]  # 删掉“甲”

    current_yaml = parsed.as_yaml()
    assert _lines(current_yaml) == [
        "# 中文注释",
        "items:",
        "- 乙",
        "  # 中间中文注释",
        "- 丙",
        "tail: x",
    ]

    first = parsed["items"][0]  # 数据上是“乙”
    # 陈旧坐标：start/end 都报 5，而“乙”实际在第 3 行。
    assert first.start_line == 5
    assert first.end_line == 5
    # lines() 取出的也是原输入第 5 行的旧邻居“丙”，而非当前节点“乙”。
    # lines() 内部的 RoundTripDumper 默认转义非 ASCII，返回的是反斜杠 u
    # 转义形式；下一行再用 unicode_escape 解码回可读的“丙”，便于核对。
    assert first.lines() == '- "\\u4E19"'
    assert first.lines().encode("ascii").decode("unicode_escape") == '- "丙"'
    assert _fragment(current_yaml, first.start_line) == "- 丙"
    assert "乙" not in first.lines().encode("ascii").decode("unicode_escape")

    second = parsed["items"][1]  # 数据上是“丙”
    # 末元素坐标彻底失效：start_line 大于 end_line，lines() 为空。
    assert second.start_line == 6
    assert second.end_line == 5
    assert second.lines() == ""
    # 报告的第 6 行在当前文件里其实是下一个键，而不是任何列表项。
    assert _fragment(current_yaml, second.start_line) == "tail: x"


def test_nested_sequence_delete_leaves_stale_coordinates_and_broken_revalidate():
    """
    【仅指原输入】嵌套列表删除整组后，存活内层元素的坐标只指原输入布局，
    且基于该陈旧节点重新验证会直接抛 IndexError（而不是带定位的校验错误）。
    """
    parsed = load(
        "# 中文注释开头\n"
        "matrix:\n"
        "  - - 1\n"
        "    - 2\n"
        "  # 组之间的中文注释\n"
        "  - - 10\n"
        "    - 20\n"
        "tail: ok\n",
        Map({"matrix": Seq(Seq(Int())), "tail": Str()}),
    )
    del parsed["matrix"][0]  # 删掉包含 1、2 的整组

    current_yaml = parsed.as_yaml()
    assert _lines(current_yaml) == [
        "# 中文注释开头",
        "matrix:",
        "- - 10",
        "  - 20",
        "tail: ok",
    ]

    ten = parsed["matrix"][0][0]  # 实际在第 3 行
    twenty = parsed["matrix"][0][1]  # 实际在第 4 行

    # 两个存活元素都报出 start_line(5) > end_line(4)、lines() 为空。
    for node in (ten, twenty):
        assert node.start_line == 5
        assert node.end_line == 4
        assert node.lines() == ""
        # 报告行落到当前文件第 5 行的 tail，而不是真正的数据行。
        assert _fragment(current_yaml, node.start_line) == "tail: ok"

    # 真正的数据行（用于对照，证明上面的坐标不是当前位置）。
    assert _fragment(current_yaml, 3) == "- - 10"
    assert _fragment(current_yaml, 4) == "  - 20"

    # 对陈旧节点重新验证无法给出带定位的错误，而是越界崩溃。
    with pytest.raises(IndexError):
        twenty.revalidate(Bool())


def test_child_reference_held_before_edit_keeps_original_input_coordinates():
    """
    【仅指原输入】编辑前抓住的子节点引用，在 YAML 值赋值（内部 fork 新文档）
    后仍挂在冻结的原输入上；其坐标套到当前文件会越界，重新取值才能得到
    当前文件坐标。
    """
    parsed = load(ORIGINAL_DOCUMENT, MAPPING_SCHEMA)
    stale_replicas = parsed["replicas"]  # 编辑前抓住引用

    parsed["name"] = load("renamed")  # YAML 值赋值 -> fork，原文档被冻结
    del parsed["description"]

    current_yaml = parsed.as_yaml()
    current_lines = _lines(current_yaml)
    frozen_yaml = dump(stale_replicas.whole_document(), Dumper=RoundTripDumper)
    frozen_lines = _lines(frozen_yaml)

    # 两份文档已经不是同一个对象，行数也不同。
    assert stale_replicas.whole_document() is not parsed.whole_document()
    assert len(frozen_lines) == 11  # 冻结的原输入
    assert len(current_lines) == 8  # 编辑后的当前文件

    # 旧引用坐标（10）在冻结原输入里能取到 replicas 行。
    assert stale_replicas.start_line == 10
    assert _fragment(frozen_yaml, stale_replicas.start_line) == "replicas: '3'"
    # 同一行号在当前文件里越界——不能当成当前文件位置。
    assert stale_replicas.start_line > len(current_lines)
    assert _fragment(current_yaml, stale_replicas.start_line).startswith("<越界")

    # 重新从编辑后的文档取值，坐标才锚定当前文件（第 7 行）。
    fresh_replicas = parsed["replicas"]
    assert fresh_replicas.start_line == 7
    assert _fragment(current_yaml, fresh_replicas.start_line) == "replicas: 3"
