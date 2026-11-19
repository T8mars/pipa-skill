# Pipa Character Replacement

## Service links and offers

| Service | Access | Offer |
| --- | --- | --- |
| Zhenzhen AI | [Open Zhenzhen AI](https://www.zzai.app) | Free GPT Image 2.6 |
| RunningHub | [Mainland China](https://www.runninghub.cn?inviteCode=kol01-rh099) · [International](https://www.runninghub.ai?inviteCode=rh-v1121) | Three free H3 runs per day, plus coupons |
| Chenyu | [Open Chenyu](https://www.chenyu.cn/console/login?invitationCode=NGT5S4) | T8-exclusive coupons listed below |

| Chenyu offer | Availability | Coupon code |
| --- | --- | --- |
| 0.1 = 5 | 300 coupons | `W6TGFF` |
| 30 = 45 | 300 coupons | `T8AIGC` |
| 50 = 100 | Available to every fan, once per account | `50VS50` |

Search for **t8star** to find new mirror sites as they are added.

[简体中文](README.md)

Replace the two roles in a fixed, 33-shot sequence. Reference 1 plays the character who loses out at the end. Reference 2 plays the other character. The included generator uses RunningHub. The skill preserves the original edit using shot notes, a cast map, frame tables and a review before final assembly.

## Quick start

1. Obtain the original motion clips and `41888056471-1-192.mp3` from the original remake package. Use a separate working copy and keep the 33 motion files in `补齐生成用`.
2. Install this repository as `pipa-character-replacement` in your agent's skills directory. Copy the contents of `assets/project-template` into the work copy, keeping the directory layout. On Windows, use Python 3.10+, install `requirements.txt` and put FFmpeg and ffprobe on your PATH.
3. Give the agent the two reference images, their roles and the work-copy path. The expected names are `替换用参考图/一号.png` and `替换用参考图/二号.png`. Prepare the shot prompts in `提示词` and place your own RH key in `api_key.txt` inside the work copy.
4. Run `python validate_prompts.py` before the first trial. Running `python rh_batch.py submit 001` submits one paid shot. Use `status` and `download` with the same script and work copy to resume. The default concurrency is three.
5. After all 33 shots pass technical and visual review, run `python assemble.py --output pipa-final.mp4`. The resulting film is in `完成分段/最终组装`.

The [project template](assets/project-template) contains the runner, prompt validator, assemblers and shot data. Run `python verify_runner.py` and `python verify_updates.py` there for offline regression checks.

Original remake package and workflow by [wuwukasi](https://space.bilibili.com/670281046). Skill integration and maintenance by [T8star](https://space.bilibili.com/385085361).
