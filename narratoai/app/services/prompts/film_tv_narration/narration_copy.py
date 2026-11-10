#!/usr/bin/env python
# -*- coding: UTF-8 -*-

"""
@Project: 影视解说-解说文案
@File   : narration_copy.py
@Description: 生成可供用户审核修改的影视解说正文
"""

from ..base import ParameterizedPrompt, PromptMetadata, ModelType, OutputFormat


class NarrationCopyPrompt(ParameterizedPrompt):
    """影视解说正文生成提示词"""

    def __init__(self):
        metadata = PromptMetadata(
            name="narration_copy",
            category="film_tv_narration",
            version="v1.0",
            description="基于剧情理解和字幕生成可审核修改的影视解说正文，不绑定时间戳",
            model_type=ModelType.TEXT,
            output_format=OutputFormat.TEXT,
            tags=["影视", "解说文案", "电影解说", "剧情承接", "用户审核"],
            parameters=[
                "drama_name",
                "drama_genre",
                "plot_analysis",
                "subtitle_content",
                "narration_language",
                "narration_word_count",
            ],
        )
        super().__init__(metadata, required_parameters=["drama_name", "plot_analysis", "subtitle_content"])

        self._system_prompt = (
            "你是一位影视解说口播创作者，写的是给人耳朵听的口播稿，不是书面说明文。"
            "你只输出可供用户审核修改的解说正文，不要输出JSON、时间戳、编号、标题、解释或Markdown。"
        )

    def get_template(self) -> str:
        return """# 影视解说正文创作任务

## 目标
为影视作品《${drama_name}》创作一份可直接给用户审核修改的解说文案正文。此阶段不做画面匹配，不输出时间戳。

## 剧情理解材料
<plot>
${plot_analysis}
</plot>

## 原始字幕
<subtitles>
${subtitle_content}
</subtitles>

## 输出语言
<narration_language>
${narration_language}
</narration_language>

## 用户选择的影视类型
<drama_genre>
${drama_genre}
</drama_genre>

## 用户要求的文案字数
<narration_word_count>
${narration_word_count}
</narration_word_count>

## 类型写作规则
必须按用户选择的影视类型调整表达重点，不要自行改判类型：
- 剧情/情感：突出人物选择、关系裂痕、命运压力和情绪余波。
- 悬疑/犯罪：突出线索、疑点、动机、误导和未揭开的真相。
- 动作/冒险：突出目标、危险升级、身体对抗和关键抉择。
- 喜剧/轻松：突出误会、反差、节奏包袱和人物可爱处。
- 科幻/奇幻：突出设定规则、未知威胁、世界观反差和代价。
- 历史/战争：突出时代处境、阵营选择、牺牲和局势变化。
- 恐怖/惊悚：突出异常细节、压迫感、未知危险和心理悬念。
- 自定义类型：严格服从用户填写的类型方向。

## 开头写法
开头不要套固定公式，要像真人在讲故事：先点出最抓人的画面或人物处境，再自然带出一个反常信息或悬念问题，让观众想继续听下去。不要每篇都用同一种开头句式。

## 写作规则
1. 必须使用 ${narration_language}。
2. 严格基于剧情理解和字幕事实，不编造核心情节、身份、结局。
3. 先写清楚人物动机和因果链，再写情绪点；不要只堆形容词。
4. 每句话只表达一个信息点，适合后续按句匹配画面。
5. 写给耳朵听：句子尽量短，单句优先 15-35 字；信息复杂时拆成多句；用逗号、问号、感叹号控制停顿和语气，偶尔用反问带节奏，不要每句都感叹。
6. 每 2-3 句要有明确承接，让观众知道为什么从上一幕来到下一幕。
7. 不要写"值得一提的是""不得不说""让人不禁感慨""总而言之"这类书面模板句；写完默读一遍，像书面报告就改成口语说法。
8. 总长度以 ${narration_word_count} 字为目标，允许上下浮动 10%；中日韩语言按非空白字符计数，其他语言按单词计数。不得再套用固定长度区间。
9. 不要使用编号、项目符号、章节标题或括号说明。

## 输出要求
只输出解说正文。不要输出 JSON、时间戳、代码块或任何解释。"""
