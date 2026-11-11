# -*- coding: utf-8 -*-
"""StrictYAML 对照实验：无 schema 入口 vs 显式 Str/Bool/Int/Datetime。

观察每个输入的三种形态：
  text    -- 解析后节点里保留的原始文本
  data    -- 交给 Python 的值（类型 + 值）
  redump  -- 用同一 schema 重新序列化（as_document）后的 YAML 文本
"""
from strictyaml import load, as_document, Map, Str, Bool, Int, Datetime
from strictyaml.exceptions import YAMLValidationError

VALUES = ["on", "001", "2026-11-11", "", "1.5", "yes", "0"]


def doc_of(value):
    return "k: " + value if value != "" else "k:"


def show(value, text, data, redump):
    print("  {!r:14} text={!r:14} data={!r:28} redump={!r}".format(
        value, text, "{}({})".format(type(data).__name__, data), redump))


print("=" * 84)
print("A. 无 schema 入口 load()  —— 隐式 Any()，所有标量一律按字符串处理")
print("=" * 84)
for v in VALUES:
    doc = load(doc_of(v))
    node = doc["k"]
    show(v, node.text, node.data, doc.as_yaml().strip())

print()
print("结论 A: text/data/redump 三者都原样保留，on 不会变 True，001 不会变 1，")
print("        日期不会变 datetime，空值是空字符串 '' 而不是 None。")

VALIDATORS = [Str, Bool, Int, Datetime]

for validator in VALIDATORS:
    name = validator.__name__
    schema = Map({"k": validator()})
    accepted, rejected = [], []
    for v in VALUES:
        try:
            node = load(doc_of(v), schema)["k"]
            redump = as_document({"k": node.data}, schema).as_yaml().strip()
            redump_value = redump.split(":", 1)[1].strip()
            accepted.append((v, node.text, node.data, redump_value))
        except YAMLValidationError as exc:
            rejected.append((v, exc.problem.strip()))

    print()
    print("=" * 84)
    print("B. 显式 schema {}() ".format(name))
    print("=" * 84)
    print("-- 成功但解释不同（或规范化输出）:")
    if accepted:
        for v, text, data, redump in accepted:
            tag = "原样" if (isinstance(data, str) and data == v and redump == v) else "重解释/规范化"
            print("  {!r:14} text={!r:14} data={!r:28} redump={!r:22} [{}]".format(
                v, text, "{}({})".format(type(data).__name__, data), redump, tag))
    else:
        print("  (无)")
    print("-- 拒绝的输入 (YAMLValidationError):")
    if rejected:
        for v, problem in rejected:
            print("  {!r:14} -> {}".format(v, problem))
    else:
        print("  (无)")
