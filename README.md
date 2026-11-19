# 琵琶曲人物替换 · Pipa Character Replacement

## 调用入口与优惠

| 平台 | 入口 | 优惠 |
| --- | --- | --- |
| 贞贞 AI | [贞贞 AI 调用](https://www.zzai.app) | 免费 GPT Image 2.6 |
| RunningHub | [国内站](https://www.runninghub.cn?inviteCode=kol01-rh099) · [国际站](https://www.runninghub.ai?inviteCode=rh-v1121) | 每日 3 次免费 H3 及优惠券 |
| 晨羽 | [晨羽调用](https://www.chenyu.cn/console/login?invitationCode=NGT5S4) | T8 专属优惠券，见下表 |

| 晨羽优惠 | 数量或领取条件 | 券码 |
| --- | --- | --- |
| 0.1 = 5 | 300 张 | `W6TGFF` |
| 30 = 45 | 300 张 | `T8AIGC` |
| 50 = 100 | 每位粉丝均可领取，每个账号限领一次 | `50VS50` |

更多镜像陆续更新，可搜索 **t8star**。

## 选择生成渠道

每次收到新的一组参考图，先确认使用 RunningHub、贞贞 AI 还是晨羽。用户已指定时直接采用。恢复已有任务时沿用制作目录中的渠道和任务号。在新制作目录运行以下三条命令中的一条，选择渠道这一步不计费。

```powershell
python channels.py select --provider rh
python channels.py select --provider zhenzhen --key-file "C:\MyPrivate\zhenzhen.key"
python channels.py select --provider chenyu --key-file "C:\MyPrivate\chenyu.key"
```

两种新渠道各自使用所选平台的私有 Key；文件放在技能和制作目录之外。也可以省略 `--key-file`，使用 `ZZ_API_KEY_FILE` 或 `CHENYU_API_KEY_FILE` 指向私有文件。贞贞 AI 使用 `minimax-h3-comfyui-charswap`，把有序图片 URL 写入 `images`，运动视频和帧数放在 `metadata`。晨羽使用已发布的「Wuwu-视频角色替换」工作流，先校验版本，将图片分开上传并按当前镜头角色顺序绑定节点。

每段保留原始动作帧。渠道需要补帧时，仅给 24 fps 提交副本追加末帧，最终仍按原帧表合片。提交中断时，已取得任务号的继续查询；晨羽可用 `python channels.py reconcile --attempt <本地尝试ID>` 恢复原订单。贞贞 AI 在平台核对该镜头的原任务号后，再添加 `--task-id <原任务号>`。

[English guide](README.en.md)

把两张人物参考图替换进固定的琵琶曲片段，按原镜头复核，再按原始帧数拼回整片。**一号图对应最后吃瘪的角色**，二号图对应另一位。技能包含 33 段镜头简介、角色映射、帧数表、提示词模板，以及生成、下载、复核和组装的 Python 代码。

## 开始使用

1. 从复刻包原作者处取得这套原始运动视频和配乐，另建工作副本。保留 `补齐生成用` 下的 33 个分段和原音轨 `41888056471-1-192.mp3`。
2. 将本仓库下载或克隆到技能目录，文件夹命名为 `pipa-character-replacement`。在工作副本中使用仓库 `assets/project-template` 下的脚本、帧数表及角色配置，维持它们的相对目录结构。Windows 使用 Python 3.10+，安装 `requirements.txt` 中的依赖，并让 `ffmpeg`、`ffprobe` 可从命令行调用。
3. 发给 Agent 两张人物图，说明哪个角色吃瘪，并指定工作副本路径。Agent 将参考图放至 `替换用参考图/一号.png`、`二号.png`，结合每段简介编写 `提示词/001.txt` 至 `033.txt`。使用 RH 时把自己的 RH Key 保存在工作副本的 `api_key.txt`。
4. 先校验提示词，再生成试片。每段根据角色映射上传一张或两张图。并发数由 `并发数.txt` 控制，默认 3。续跑使用原工作副本与任务记录。

在工作副本中执行：

```powershell
python validate_prompts.py
python rh_batch.py submit 001
python rh_batch.py status
python rh_batch.py download
```

`submit 001` 会向选定渠道提交第 001 段。全部 33 段通过技术与视觉复核后，运行 `python assemble.py --output 琵琶曲_完整版.mp4`。成片位于 `完成分段/最终组装`。

使用贞贞 AI 或晨羽时，已下载但画面不合格的候选可单段返工。先在 `zhenzhen_state.json` 或 `chenyu_state.json` 找到该段的 `attempt`，运行 `python rh_batch.py retry 020 <attempt>`。先前的视频归档到原候选目录。重跑后照常按新任务号验收。

## 代码和离线验证

- [技能入口](SKILL.md)说明人物映射、逐段验收和恢复方法。
- [项目模板](assets/project-template)包含 `rh_batch.py`、`validate_prompts.py`、组装脚本与全部镜头资料。
- 在项目模板目录运行 `python verify_runner.py` 和 `python verify_updates.py`，可离线检查调度、单人及双人路由、重试与断线恢复。

原始复刻包与工作流来自 [wuwukasi](https://space.bilibili.com/670281046)，技能整合与维护由 [T8star](https://space.bilibili.com/385085361) 完成。公开仓库提供技能代码与文本模板。制作素材和账号凭据由使用者在本地准备。
