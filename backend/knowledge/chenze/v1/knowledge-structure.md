# 可持续填充的主播知识结构

当前版本：0.8-game-B-asr-keyframe-v1-human-pending；日期2026-11-18（Asia/Shanghai）。入口文件knowledge-base.json，字段契约knowledge-schema.json，来源账本sources.json，规则主表rules-draft.json。

## 四层事实边界

|层|可放什么|不可放什么|示例与证据|
|---|---|---|---|
|person_public_facts 真人公开事实|账号映射、公开职业描述、明确时段的公开同台或合作|AI剧情、粉丝CP、猜测性格、私人资料、未经核实法律指控|P01/P02：陈泽-对应抖音号9896719.；未据此确认法定姓名或平台认证|
|program_content 直播/节目内容|具体节目/场次中的发言、行为、游戏规则；原片时间轴；二手分析必须标reported|把一句节目话升级为永久世界观；把标题或弹幕当实际台词|S12报道叫名停餐规则，但原片未核，状态reported_not_av_verified|
|derivative_fiction 二创虚构|作品/角色/虚构宇宙、作者自述方法、观看该作的reaction关系|把陈美泽故事当真人情史；把原型与角色合并实体|S09采访中的陈美泽是AI角色，独立fiction_entity_id，只有based_on关系|
|editing_recommendation 剪辑建议|通用规范、目标栏目试验、梗语境保护、二创作者方法，分别标性质|把规范叫作陈泽风格；以一般人物事实支持固定秒数；把创作意图叫效果验证|D01定义保真/句尾/上限拒绝；CZ-C01是待验双人游戏标注方案|

实体关系只许“账号由人物运营”“公开同场”“节目中扮演”“虚构角色based_on原型”等明确类型。节目同台不推导私交、公司职级或恋爱；引用一句话不推导稳定人格。

## 核心记录与持久ID

### 1. entities
entity_id、entity_type(person/account/program/work/fictional_character)、display_name、aliases(每个带证据)、layer、source_ids、verification_status、valid_from/to（未知null）。
真人、账号、节目和AI角色各一个ID，永不因同名合并。不能用抖音号末尾标点不一致静默归并；本目标已核为9896719.。

### 2. claims（原子化主张）
claim_id、layer、subject_id、predicate、object_value、attribution（是谁说/报道）、source_ids、evidence_status、supported_scope、limits、valid_at、contradiction_ids、supersedes。
主张必须是单一可判定陈述。把“页面写了某话”“某人确实说了”“某事确实发生”拆开，避免互相升级。S12的规则是文章所述；T01/T02是网页有这段文本；原音未验不能填精确speaker_quote。

### 3. program_segments / samples
sample_id、program_id、asset_source_id、actual_playable_url、source_publication_time、page_duration、probed_duration、content_type、original_or_repost、same_session_group、source_lineage、version（弹幕/纯净）、review_status、rights_status。
segment_start/end是播放器原时间（秒），来源清楚才填；时间码、画面、语音、字幕/音效观测分字段。metadata_only样本这些字段必须null/未验，不能由文章“近10分钟”填时间码。
structure内setup/conflict/reaction/payoff/tail分别可为空；空表示未知或absent，而非自动补剧情。尾段额外记sentence_end、semantic_closure、needed_following_response及prohibited_cut_points。

### 4. derivative_works
work_id、creator_entity_id、universe_id、fiction_entity_ids、based_on_entity_ids、source_ids、original_page_source_id、rights_status。
主创方法与实际成片效果分开。原作者采访可证创作意图，不是陈泽本人规范；播放量不证因果。reaction_segment需指向被看作品，不能取代作品或真人事件。

### 5. rules
rule_id、rule_kind、trigger、action_opcode、action、required_inputs、avoid、source_ids、evidence_bindings、confidence、verification_status、exceptions、failure_conditions、postcondition、sentence_tail_policy、duration_failure_policy、asr_review_policy、numeric_edit_parameters、enabled_for_automatic_editing。

四个rule_kind：
- universal_fidelity_and_provenance：通用保真/出处/授权/研究门槛，单独general-fidelity-rules.json。
- chenze_format_candidate：陈泽相关栏目/口头禅/回应的标注试验，不称其独有风格。
- subject_context_guardrail：专属梗的防错保护，不是吸引流量技巧。
- derivative_creator_method_candidate：二创作者经验，单独derivative-method-candidates.json，不能自动迁入真人切片。

后两类目标相关候选与语境保护分别在chenze-style-candidates.json标清类别。validated_chenze_style_rules目前为空；所有开关false。

source_ids必须具体；evidence_bindings说明每个引用只支持什么。D01是规范依据，不是公开事实证据。公开文章只支持场景/意图/上下文，不替未经验证的剪辑数值背书。

### 6. review_queue / validation_runs
任务ID、目标source/sample/rule、待回答问题、允许方法、所需结果、状态、父记录。验收记录还需支持样本、独立场次数、反例、盲测样本、审片结论和规则版本。暂无实际视听验收记录，不能伪填通过。

## 增量工作流

1. 新来源先入sources，保留访问状态、原文短摘录、作者、时间和证据家族；同文转载不加独立票。
2. 新事实入claims，而非直接写散文简介。引用与断言分层，矛盾相连，不默默覆盖。
3. 页面元信息入sample；只有实际播放/合法媒体核过才升级review_status。页面显示时长与媒体测量时长分开。
4. 视听流程补原时间码、说话人及五拍结构；字幕需人工对照原音。先得到可理解的完整单元，再测试请求上限。
5. 新建议先选rule_kind，引用记录只许支持其范围。风格候选先禁用，找到反例并跨场验证；不得因为身份或节目页已核就自动启用。
6. 每次生成knowledge QA，报告解决项与未解决项；不在不同文件里同时保留未标历史的相互矛盾状态。快照只用于历史，不作当前检索入口。

## 当前完成与未完成

本人账号及8个原发节目页面的发布元信息已由父代理核查；其中V19原来就在候选表，另外7页是新补原发锚点，不算7个已研究的视频内容样本。页面显示作品212仅对应当前主页快照。

已有1条正常下载预告(M01)及独立watchVideo工具粗观测(AV01)，不是本子代理看片；实测64.433333秒。没有人工原音验收逐字稿或逐帧切点；粗场景观测单独存av-observations.json，不能把模型观测提升为人类精审或现实动机/权利结论。第一集下下载仍进行，后续等待正片不同区间视听，不继续无限搜索。

## 正片部分视听增量
M02原片与完整低清代理已登记，AV02/AV03仅覆盖已报告的首段与末段；剩余450—1350秒未完成视听。新增program_content的content_subtype=programme_fiction用于原节目自身的老年假设/表演段，区别于粉丝derivative_fiction，也禁止提升为person_public_facts。原片时间映射为模型粗检索，最终剪点须回原片核音画与句尾；不把完整分段文件当完整观看。

## 全程分段模型观测更新
AV04/AV05已补齐中段，O01标audiovisual_tool_full_segmented；complete_segmented_model_observation=true，但complete_human_or_frame_review=false。粗模型结果冲突存evidence-conflicts，不静默覆盖。QC-TIME01/fit_qc.py用精确区间算术及人工语义门槛检验FIT；模型标签不直接进入批准结果。当前待直播样本和人工验收，旧partial段落仅记录历史。

## 游戏转载B
V26/M04/AV09登记为同名未验证账号转载。方法是local_asr_plus_keyframes，review_status=local_asr_keyframe_coarse。出镜者不得写入person_public_facts。跨类型导航只比较已被证据支撑的结构，不迁移密度或立场模板。
