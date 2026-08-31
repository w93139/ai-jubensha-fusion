"""Idempotently install the three launch mysteries for 人生海海."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models.background_story import BackgroundStoryDBModel
from src.db.models.character import CharacterDBModel
from src.db.models.evidence import EvidenceDBModel
from src.db.models.game_phase import GamePhaseDBModel
from src.db.models.location import LocationDBModel
from src.db.models.script_model import ScriptDBModel, ScriptStatus
from src.schemas.evidence_type import EvidenceType
from src.schemas.game_phase import GamePhaseEnum


PHASES = [
    (GamePhaseEnum.BACKGROUND, "案件背景", "了解案发现场、死者与共同规则。"),
    (GamePhaseEnum.INTRODUCTION, "自我介绍", "以角色身份介绍自己与死者的关系。"),
    (GamePhaseEnum.EVIDENCE_COLLECTION, "第一轮搜证", "选择地点，寻找表层线索。"),
    (GamePhaseEnum.INVESTIGATION, "线索公开与质询", "决定是否公开证据并向其他角色提问。"),
    (GamePhaseEnum.EVIDENCE_COLLECTION, "第二轮搜证", "回到现场，寻找决定性证据。"),
    (GamePhaseEnum.DISCUSSION, "圆桌讨论", "还原时间线、动机与作案手法。"),
    (GamePhaseEnum.VOTING, "最终投票", "指认你认为最可能的真凶。"),
    (GamePhaseEnum.REVELATION, "真相复盘", "公开完整真相与证据链。"),
]


SCRIPTS = [
    {
        "title": "商业谋杀案：零点审计",
        "description": "上市前夜，曜石科技董事长顾正阳死在封闭办公室。四位核心高管都隐瞒了足以毁掉职业生涯的秘密，但只有一人利用他的胰岛素笔制造了无痕谋杀。",
        "duration": 40,
        "difficulty": "MEDIUM",
        "category": "现代商战",
        "tags": ["现代", "商战", "本格", "新手友好"],
        "story": {
            "title": "零点前的董事会",
            "setting": "曜石科技准备在次日上午提交上市材料。暴雨封锁了园区，董事长办公室所在的顶层只有四名高管可以进入。",
            "incident": "22:10，顾正阳被发现倒在办公桌旁。门禁没有陌生人记录，咖啡无毒，桌上的急救胰岛素笔却被人换过笔芯。",
            "victim": "顾正阳，52 岁，曜石科技创始人，糖尿病患者。案发当晚准备公布内部审计结论并撤换一名高管。",
            "scope": "顶层董事长办公室、财务室、服务器间和茶水间。重点还原 21:20 至 22:10 的行动。",
            "rules": "每人都可以隐瞒自己的秘密，但不得捏造系统给出的证据。两轮搜证后完成投票。",
            "method": "罗凯提前把顾正阳急救笔中的常规胰岛素换成高浓度速效制剂，并在顾发病时故意建议其自行注射，造成致命低血糖。",
            "location": "董事长办公室",
            "discovery": "22:10",
            "truth": {
                "truth_recap": "财务总监罗凯挪用上市募集准备金，得知顾正阳将在零点前公布审计报告。21:32 他借送预算表进入办公室，替换胰岛素笔芯；21:58 又在茶水间诱导顾喝下高糖饮料，使顾误以为血糖异常并自行注射。罗凯随后删除门禁副本并伪造自己一直在财务室的假象。",
                "evidence_chain": ["笔芯批号属于财务室医药箱", "21:32 门禁离线缓存记录罗凯进入", "冷藏领用单有罗凯缩写", "被碎纸机切碎的审计页指向其秘密账户"],
                "innocent_goal": "锁定罗凯并说明换笔芯、诱导注射与掩盖门禁记录的完整链条。",
                "murderer_goal": "隐藏换药事实，把审计动机嫁祸给其他高管。",
            },
        },
        "characters": [
            ("沈青", 29, "董事长助理", "女", False, "你负责顾正阳的日程与用药提醒。21:20 你把一份私人会面从日历中删除，21:45 离开办公室去打印材料。", "你与顾正阳曾有一段感情，删除日历是怕关系曝光；你看见罗凯 21:32 拿着蓝色文件夹进入办公室。", "保护私人关系，同时说清你目击到的关键时间。"),
            ("罗凯", 44, "财务总监", "男", True, "你主管上市财务。你声称 21:20 后一直在财务室核对报表，只在 22:05 听到呼救。", "你挪用了两千万准备金。你替换了胰岛素笔芯、诱导顾正阳注射，并删除门禁主记录；必须隐瞒这些事实。", "制造合理的不在场证明，把审计冲突引向陈默或唐瑜。"),
            ("陈默", 36, "技术总监", "男", False, "你控制园区门禁和服务器。21:40 至 22:00 你在服务器间处理异常日志。", "你擅自向竞争公司演示过未发布算法，删除的是数据外传日志而非凶案门禁；你在离线缓存里看见罗凯的进入记录。", "解释删日志的真实原因，并推动大家核对离线门禁缓存。"),
            ("唐瑜", 39, "法务总监", "女", False, "你为董事会准备罢免决议。21:50 在茶水间与顾正阳争执，随后回会议区。", "顾正阳要求你把罗凯的审计问题压到上市后，你拒绝并秘密保留了审计附件；你看见罗凯往顾的饮料里加葡萄糖粉。", "保护职业声誉，在合适时机公开审计附件与茶水间见闻。"),
        ],
        "locations": [
            ("董事长办公室", "案发现场，办公桌、药盒和门禁终端仍保持原状。", True),
            ("财务室", "上锁的文件柜、碎纸机和一只公用医药箱。", False),
            ("服务器间", "保存门禁离线缓存和系统维护记录。", False),
            ("茶水间", "咖啡机、饮料杯和清洁柜集中在这里。", False),
        ],
        "evidence": [
            ("被替换的胰岛素笔芯", "董事长办公室", "笔身属于顾正阳，但笔芯批号与其处方不符，浓度是日常剂量的十倍。", EvidenceType.PHYSICAL, "关键"),
            ("蓝色文件夹纤维", "董事长办公室", "药盒卡扣上夹着蓝色纸纤维，与财务部预算文件夹材质一致。", EvidenceType.PHYSICAL, "重要"),
            ("碎裂的审计报告", "财务室", "拼合后可见一笔流向罗凯控制账户的两千万异常转账。", EvidenceType.DOCUMENT, "关键"),
            ("冷藏医药箱领用单", "财务室", "高浓度速效胰岛素在案发当天被签字领出，签名缩写为 L.K.。", EvidenceType.DOCUMENT, "关键"),
            ("门禁离线缓存", "服务器间", "主日志虽被删除，控制器仍记录罗凯 21:32 进入、21:37 离开董事长办公室。", EvidenceType.DOCUMENT, "关键"),
            ("数据外传告警", "服务器间", "陈默删除的日志实际记录算法外传，时间为 21:46，与换药无关。", EvidenceType.DOCUMENT, "辅助"),
            ("含葡萄糖粉的杯子", "茶水间", "杯中无毒，只有大量葡萄糖；这会让顾正阳误判身体状况并使用胰岛素笔。", EvidenceType.PHYSICAL, "重要"),
            ("清洁柜中的手套", "茶水间", "手套内侧检出罗凯的皮屑，外侧有与笔芯相同的药液残留。", EvidenceType.PHYSICAL, "关键"),
        ],
    },
    {
        "title": "神秘的书房：灰烬密室",
        "description": "收藏家何文舟在反锁书房中身亡，壁炉只有冷灰，窗户从内扣住。遗嘱、赝品与一条被封死的旧烟道，把四名亲近者困在同一场谎言里。",
        "duration": 45,
        "difficulty": "HARD",
        "category": "古宅密室",
        "tags": ["古宅", "密室", "本格", "收藏"],
        "story": {
            "title": "灰烬密室",
            "setting": "临江何宅因台风停电。收藏家何文舟邀请四人鉴定即将捐赠的名画，随后独自进入二楼书房。",
            "incident": "21:30，书房门被撞开，何文舟倒在书桌前。门窗均从内锁住，尸表无明显外伤，壁炉里只有前一日留下的冷灰。",
            "victim": "何文舟，67 岁，收藏家。他发现馆藏名画被调包，并准备在当晚公开鉴定结果。",
            "scope": "书房、修复室、画廊和温室。重点解释密室如何在凶手离开后才致死。",
            "rules": "角色秘密不等同于杀人动机。请用证据解释死亡机制、密室与时间差。",
            "method": "林墨在修复书柜时接通隐藏烟道，把修复室炭炉的一氧化碳延时导入书房；何文舟反锁后才中毒身亡。",
            "location": "二楼书房",
            "discovery": "21:30",
            "truth": {
                "truth_recap": "修复师林墨长期用赝品替换何文舟的藏画。何准备公布检测结果，林墨便利用老宅共用烟道制造延时密室：20:40 点燃无烟炭，打开书柜后的暗阀，21:05 离开；何文舟 21:10 反锁书房，约十五分钟后中毒。林墨再把炭炉移入水槽降温，伪装成从未点燃。",
                "evidence_chain": ["血液呈一氧化碳中毒特征", "书柜后暗阀有新鲜修复蜡", "修复室炭炉灰烬内部仍温热", "赝品颜料与林墨私人订单一致"],
                "innocent_goal": "解释延时烟道如何形成密室，并确认赝品动机。",
                "murderer_goal": "把死亡解释成心脏病，或将遗产矛盾嫁祸何舒影。",
            },
        },
        "characters": [
            ("何舒影", 32, "死者之女", "女", False, "你负债严重，父亲当晚拒绝提前分割遗产。20:55 你在画廊与他激烈争吵。", "你偷拿过遗嘱副本，发现父亲准备把大部分藏品捐出；但你离开时父亲仍清醒，并看见林墨袖口沾有黑灰。", "承认遗产冲突，证明你离开后的时间线。"),
            ("周既白", 48, "博物馆馆长", "男", False, "你负责接收捐赠藏品，20:30 到画廊做初检，之后一直整理目录。", "你曾为保住展览名额而隐瞒一幅画的初检异常；你在目录中记录了三幅画框重量不符。", "公开重量记录，避免被认为参与赝品交易。"),
            ("林墨", 35, "古画修复师", "男", True, "你维护何宅藏画和老宅木作。你声称 20:45 后一直在温室修补画框。", "你调包藏画牟利，并利用修复室炭炉、共用烟道和书柜暗阀制造一氧化碳延时密室。", "否认炭炉被点燃，强调何文舟有心脏病并煽动遗产矛盾。"),
            ("秦越", 41, "家庭医生", "女", False, "你熟悉何文舟的心脏病史。21:30 参与破门并首先检查遗体。", "你曾违规给何文舟开镇静药，怕处方曝光；但遗体樱桃红色尸斑让你怀疑一氧化碳中毒。", "说明医学判断，并区分镇静药与真正死因。"),
        ],
        "locations": [
            ("二楼书房", "反锁的案发现场，书柜背靠旧烟道。", True),
            ("修复室", "存放颜料、修复蜡、炭炉与清洗水槽。", False),
            ("私人画廊", "陈列待捐赠藏画和鉴定目录。", False),
            ("玻璃温室", "林墨声称一直工作的地方，可通往修复室侧门。", False),
        ],
        "evidence": [
            ("樱桃红色尸斑", "二楼书房", "遗体皮肤呈异常樱桃红色，符合一氧化碳中毒而非普通心脏病。", EvidenceType.PHYSICAL, "关键"),
            ("书柜后的暗阀", "二楼书房", "暗阀连通修复室烟道，旋钮上覆盖尚未完全硬化的新鲜修复蜡。", EvidenceType.PHYSICAL, "关键"),
            ("内热外冷的炭灰", "修复室", "炭炉表面被水浇冷，灰堆中心仍有余温，证明一小时前曾燃烧。", EvidenceType.PHYSICAL, "关键"),
            ("带黑灰的修复手套", "修复室", "手套属于林墨，黑灰成分与炭炉一致，指尖还有暗阀上的修复蜡。", EvidenceType.PHYSICAL, "关键"),
            ("画框重量目录", "私人画廊", "周既白记录三幅名画比历史档案轻，说明画布已被替换。", EvidenceType.DOCUMENT, "重要"),
            ("赝品颜料光谱", "私人画廊", "所谓百年颜料含现代合成蓝，与林墨私人采购批次完全一致。", EvidenceType.DOCUMENT, "关键"),
            ("未干的鞋印", "玻璃温室", "鞋印从温室侧门通向修复室，尺寸与林墨工作鞋相符，时间晚于其自称离开时间。", EvidenceType.PHYSICAL, "重要"),
            ("被藏起的阀门草图", "玻璃温室", "旧宅烟道草图标出书柜暗阀，背面有林墨计算气体扩散时间的铅笔数字。", EvidenceType.DOCUMENT, "关键"),
        ],
    },
    {
        "title": "校园谜案：第七码",
        "description": "研究生夏琳在成果答辩前夜失踪，校园广播却每隔半小时播放她的声音。四名师生都掌握一部分密码，只有一人知道她被藏在哪里。",
        "duration": 35,
        "difficulty": "EASY",
        "category": "校园悬疑",
        "tags": ["校园", "失踪", "密码", "轻推理"],
        "story": {
            "title": "第七码",
            "setting": "澄海大学封闭校庆彩排。研究生夏琳发现自己的算法成果被冒名申报，准备在次日答辩会上公开证据。",
            "incident": "20:00 后夏琳失联，但校园广播在 20:30、21:00、21:30 连续播放她的求救录音。她的手机留在实验室，门禁显示无人离校。",
            "victim": "夏琳，24 岁，密码学研究生。她没有死亡，而是被药物控制后藏在废弃广播室，必须尽快找到。",
            "scope": "密码实验室、图书馆、操场器材室和旧广播站。解开录音中的位置提示并找出绑架者。",
            "rules": "本案目标是救出夏琳并指认策划者。凶手标记代表实施绑架和危害行为的人。",
            "method": "高明用含镇静剂的功能饮料迷晕夏琳，将她藏入旧广播室，并用定时播放程序伪造她仍在校园移动求救的假象。",
            "location": "废弃广播室",
            "discovery": "21:40",
            "truth": {
                "truth_recap": "实验助教高明剽窃夏琳的算法并以自己名义申报专利。夏琳准备公开版本记录，高明便在 19:45 给她含镇静剂的饮料，将她从器材通道转移到旧广播室；随后用三段预录音和被篡改的时间戳制造复杂路线。录音每段第七码组成 B-307，正是旧广播室门牌。",
                "evidence_chain": ["饮料瓶检出镇静剂与高明指纹", "Git 提交记录证明夏琳原创", "器材车轮印通往旧广播站", "定时播放脚本登录账户属于高明"],
                "innocent_goal": "在时间内解出 B-307、救出夏琳并锁定高明。",
                "murderer_goal": "拖延搜查，把剽窃动机嫁祸给竞争学生。",
            },
        },
        "characters": [
            ("陈雨", 23, "夏琳室友", "女", False, "你最后一次正常见到夏琳是在 19:35。她让你保管一张写着七组数字的便签。", "你偷偷报名了同一奖学金，删过与夏琳争吵的聊天记录；便签其实是她用来验证录音真假的取码规则。", "承认争吵，尽快公开七组数字的用途。"),
            ("徐然", 25, "密码社社长", "男", False, "你与夏琳共同维护代码仓库，20:10 在图书馆下载过项目历史。", "你曾借用夏琳的代码参加比赛，但 Git 历史能证明核心算法最早由她提交；你听出求救录音背景钟声完全相同。", "用版本记录洗清嫌疑，并指出录音是预先制作的。"),
            ("高明", 30, "实验室助教", "男", True, "你管理实验室门禁和广播设备。你声称 19:30 后一直在礼堂调试音响。", "你剽窃夏琳成果，用镇静剂迷晕她并藏在 B-307；你设置定时广播、修改门禁时间戳。", "拖延大家解码，把动机引向徐然，并否认去过器材室。"),
            ("苏晴", 34, "研究生辅导员", "女", False, "你负责校庆安全，掌握废弃楼宇钥匙。20:20 你在操场处理器材登记。", "你曾压下夏琳对高明的投诉，怕影响学院评估；你发现一辆器材车被高明借走却没有归还记录。", "弥补此前失职，公开投诉邮件和器材车记录。"),
        ],
        "locations": [
            ("密码实验室", "夏琳最后出现的地方，电脑和饮料仍留在桌上。", True),
            ("图书馆", "保存代码镜像、借阅记录和安静的录音环境。", False),
            ("操场器材室", "存放推车、音响线材和废弃楼宇钥匙。", False),
            ("旧广播站", "已停用的广播设备和编号房间集中于此。", False),
        ],
        "evidence": [
            ("含镇静剂的饮料瓶", "密码实验室", "瓶口检出高明指纹，残液含会导致数小时昏睡的镇静剂。", EvidenceType.PHYSICAL, "关键"),
            ("被修改的门禁时间戳", "密码实验室", "原始控制器比服务器记录早 17 分钟，修改操作来自高明的管理员账户。", EvidenceType.DOCUMENT, "重要"),
            ("Git 版本历史", "图书馆", "核心算法由夏琳半年前首次提交，高明的专利稿晚了四个月且复制了相同注释。", EvidenceType.DOCUMENT, "关键"),
            ("三段相同的钟声波形", "图书馆", "三次求救录音的背景钟声波形完全一致，证明它们来自同一次预录。", EvidenceType.AUDIO, "重要"),
            ("器材车轮印", "操场器材室", "沾有旧广播站特有的红色墙灰，车轮槽中还夹着夏琳外套的纤维。", EvidenceType.PHYSICAL, "关键"),
            ("无归还记录的借车单", "操场器材室", "19:42 高明借走器材车，签名后没有填写归还时间。", EvidenceType.DOCUMENT, "关键"),
            ("定时播放脚本", "旧广播站", "脚本在 20:30、21:00、21:30 播放三段录音，创建账户为 gaoming。", EvidenceType.DOCUMENT, "关键"),
            ("第七码门牌提示", "旧广播站", "每段录音按便签取第七码后组成 B307；B-307 门后能听见微弱呼吸声。", EvidenceType.DOCUMENT, "关键"),
        ],
    },
]


def database_url() -> str:
    return (
        f"postgresql+psycopg://{os.getenv('DB_USER', 'jubensha')}:{os.getenv('DB_PASSWORD', '')}"
        f"@{os.getenv('DB_HOST', 'localhost')}:{os.getenv('DB_PORT', '5432')}/{os.getenv('DB_NAME', 'jubensha')}"
    )


def install() -> None:
    session = sessionmaker(bind=create_engine(database_url()))()
    created = []
    try:
        for data in SCRIPTS:
            if session.query(ScriptDBModel).filter_by(title=data["title"]).first():
                continue
            script = ScriptDBModel(
                title=data["title"], description=data["description"], author="人生海海内容组",
                player_count=4, duration_minutes=data["duration"], difficulty=data["difficulty"],
                category=data["category"], tags=data["tags"], status=ScriptStatus.PUBLISHED,
                is_public=True, price=0, rating=0, play_count=0,
            )
            session.add(script)
            session.flush()

            story = data["story"]
            session.add(BackgroundStoryDBModel(
                script_id=script.id, title=story["title"], setting_description=story["setting"],
                incident_description=story["incident"], victim_background=story["victim"],
                investigation_scope=story["scope"], rules_reminder=story["rules"],
                murder_method=story["method"], murder_location=story["location"],
                discovery_time=story["discovery"], victory_conditions=story["truth"],
            ))
            for name, age, profession, gender, murderer, background, secret, objective in data["characters"]:
                session.add(CharacterDBModel(
                    script_id=script.id, name=name, age=age, profession=profession, gender=gender,
                    is_murderer=murderer, is_victim=False, background=background, secret=secret,
                    objective=objective, personality_traits=["谨慎", "有秘密"],
                ))
            for name, description, crime_scene in data["locations"]:
                session.add(LocationDBModel(
                    script_id=script.id, name=name, description=description,
                    searchable_items=["环境", "文件", "痕迹"], is_crime_scene=crime_scene,
                ))
            for name, location, description, evidence_type, importance in data["evidence"]:
                session.add(EvidenceDBModel(
                    script_id=script.id, name=name, location=location, description=description,
                    significance=description, evidence_type=evidence_type, importance=importance,
                    is_hidden=True,
                ))
            for index, (phase, name, description) in enumerate(PHASES):
                session.add(GamePhaseDBModel(
                    script_id=script.id, phase=phase, name=name, description=description, order_index=index,
                ))
            created.append(data["title"])
        session.commit()
        print(f"installed={created or 'none'}")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    install()
