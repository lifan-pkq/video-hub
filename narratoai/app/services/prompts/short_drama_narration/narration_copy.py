#!/usr/bin/env python
# -*- coding: UTF-8 -*-

"""
@Project: 短剧解说-解说文案
@File   : narration_copy.py
@Description: 生成可供用户审核修改的短剧解说正文
"""

from ..base import ParameterizedPrompt, PromptMetadata, ModelType, OutputFormat


class NarrationCopyPrompt(ParameterizedPrompt):
    """短剧解说正文生成提示词"""

    def __init__(self):
        metadata = PromptMetadata(
            name="narration_copy",
            category="short_drama_narration",
            version="v1.0",
            description="基于剧情理解和字幕生成可审核修改的短剧解说正文，不绑定时间戳",
            model_type=ModelType.TEXT,
            output_format=OutputFormat.TEXT,
            tags=["短剧", "解说文案", "爆款开头", "叙事连续性", "用户审核"],
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
            "你是一位短剧解说口播创作者，写的是给人耳朵听的口播稿，不是书面说明文。你只输出可供用户审核修改的解说正文，"
            "不要输出JSON、时间戳、编号、标题、解释或Markdown。"
        )

    def get_template(self) -> str:
        return """# 短剧解说正文创作任务

## 目标
为短剧《${drama_name}》创作一份可直接给用户审核修改的解说文案正文。此阶段不做画面匹配，不输出时间戳。

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

## 用户选择的短剧类型
<drama_genre>
${drama_genre}
</drama_genre>

## 用户要求的文案字数
<narration_word_count>
${narration_word_count}
</narration_word_count>

## 类型写作规则
必须按用户选择的短剧类型调整表达重点，不要自行改判类型：
- 霸总/甜宠：突出误会、身份差、暧昧拉扯、守护感和情绪反差。
- 逆袭/复仇：突出羞辱、反击、打脸、身份揭露和爽点升级。
- 家庭伦理：突出亲情撕扯、秘密、委屈、选择和道德冲突。
- 古装/权谋：突出身份、局势、算计、立场和反转。
- 悬疑/犯罪：突出线索、危机、动机和未揭开的疑问。
- 都市情感：突出关系裂痕、现实压力、误会和情绪拉扯。
- 年代/乡村：突出家庭处境、人情压力、生活困境和命运转折。
- 自定义类型：严格服从用户填写的类型方向。

## 开头写法
开头不要套固定公式，要像真人在讲故事：先点出最抓人的处境或反差，再自然带出情绪冲突和悬念，让观众想继续听下去。不要每篇都用同一种开头句式。

## 写作规则
1. 必须使用 ${narration_language}。
2. 严格基于剧情理解和字幕事实，不编造核心情节、身份、结局。
3. 先写完整故事线，再写金句；不要只堆爆点。
4. 每句话只表达一个信息点，适合后续按句匹配画面。
5. 写给耳朵听：句子尽量短，单句优先 15-35 字；信息复杂时拆成多句；用逗号、问号、感叹号控制停顿和语气，偶尔用反问带节奏，不要每句都感叹。
6. 每 2-3 句要有明确因果承接，让观众知道为什么从上一幕来到下一幕。
7. 不要写"值得一提的是""不得不说""让人不禁感慨""总而言之"这类书面模板句；写完默读一遍，像书面报告就改成口语说法。
7. 总长度以 ${narration_word_count} 字为目标，允许上下浮动 10%；中日韩语言按非空白字符计数，其他语言按单词计数。不得再套用固定长度区间。
9. 不要使用编号、项目符号、章节标题或括号说明。

## 输出要求
只输出解说正文。不要输出 JSON、时间戳、代码块或任何解释。"""
