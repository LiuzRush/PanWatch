from pathlib import Path

from src.collectors import sector_flow_collector


def _fake_clist_payload():
    return {
        "data": {
            "diff": [
                {
                    "f12": "BK0475",
                    "f14": "半导体",
                    "f2": 1234.5,
                    "f3": 2.5,
                    "f6": 10_000_000,
                    "f62": 1_000_000,
                    "f184": 10.0,
                    "f66": 400_000,
                    "f69": 4.0,
                    "f72": 300_000,
                    "f75": 3.0,
                    "f78": 200_000,
                    "f81": 2.0,
                    "f84": -100_000,
                    "f87": -1.0,
                }
            ]
        }
    }


def _fake_history_payload():
    return {
        "data": {
            "code": "BK0475",
            "name": "半导体",
            "klines": [
                "2026-07-10,1000,-100,200,300,600,1,-0.1,0.2,0.3,0.6,1234,2.5,10000,20000",
                "2026-07-13,-500,100,-200,-150,-250,-0.5,0.1,-0.2,-0.15,-0.25,1200,-1.2,9000,18000",
            ],
        }
    }


def test_sector_flow_rank_parser(monkeypatch):
    """板块资金流排行应解析主力/超大单/大单/中单/小单字段。"""
    monkeypatch.setattr(
        sector_flow_collector, "market_get", lambda *args, **kwargs: _fake_clist_payload()
    )

    collector = sector_flow_collector.EastMoneySectorFlowCollector()
    items = collector.fetch_sector_flows(sector_type="industry", mode="main_net", limit=10)

    assert len(items) == 1
    assert items[0].code == "BK0475"
    assert items[0].name == "半导体"
    assert items[0].main_net_inflow == 1_000_000
    assert items[0].small_net_inflow == -100_000


def test_sector_flow_history_parser(monkeypatch):
    """单板块历史资金流应解析日级序列。"""
    monkeypatch.setattr(
        sector_flow_collector, "market_get", lambda *args, **kwargs: _fake_history_payload()
    )

    collector = sector_flow_collector.EastMoneySectorFlowCollector()
    items = collector.fetch_sector_flow_history(board_code="BK0475", days=2)

    assert len(items) == 2
    assert items[0].date == "2026-07-10"
    assert items[0].main_net_inflow == 1000
    assert items[1].main_net_inflow == -500


def test_sector_flow_routes_and_groups(monkeypatch, tmp_path):
    """板块资金流接口、聚类配置和快照汇总可串联工作。"""
    from src.web.api import sector_flows

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        sector_flow_collector, "market_get", lambda *args, **kwargs: _fake_clist_payload()
    )

    rank = sector_flows.list_sector_flows(type="industry", mode="main_net", limit=10)
    assert rank["items"][0]["code"] == "BK0475"

    sectors = sector_flows.list_sectors(type="industry", keyword="半导体", limit=10)
    assert sectors["items"][0]["name"] == "半导体"

    stocks = sector_flows.get_sector_stock_flows("BK0475", type="industry", limit=10)
    assert stocks["items"][0]["board_code"] == "BK0475"

    payload = sector_flows.SectorFlowGroupsPayload(
        groups=[
            sector_flows.SectorFlowGroup(
                name="科技成长",
                includes=["半导体"],
                include_items=[
                    sector_flows.SectorFlowGroupSelector(
                        type="industry", code="BK0475", name="半导体"
                    )
                ],
                aliases=["硬科技"],
                order=1,
            )
        ]
    )
    saved = sector_flows.update_sector_flow_groups(payload)
    assert saved["groups"][0]["name"] == "科技成长"

    snapshot = sector_flows.refresh_sector_flow_snapshot(date="2026-07-15")
    assert snapshot["items"][0]["code"] == "BK0475"
    assert Path(tmp_path, "sector_flow_snapshots", "2026-07-15.json").exists()

    summary = sector_flows.get_sector_flow_group_summary(date="2026-07-15")
    assert summary["source"] == "snapshot"
    assert summary["groups"][0]["name"] == "科技成长"
    assert summary["groups"][0]["main_net_inflow"] == 1_000_000
    assert summary["groups"][0]["raw_main_net_inflow"] == 1_000_000


def test_sector_flow_group_preview_diagnostics(monkeypatch, tmp_path):
    """聚类规则预览应返回结构化匹配结果和配置诊断。"""
    from src.web.api import sector_flows

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sector_flows, "_is_cn_trading_time", lambda: True)
    flows = [
        {
            "code": "BK0475",
            "name": "半导体",
            "type": "industry",
            "main_net_inflow": 1_000_000,
            "main_net_inflow_pct": 10.0,
            "turnover": 10_000_000,
            "change_pct": 2.5,
        },
        {
            "code": "BK0001",
            "name": "软件开发",
            "type": "industry",
            "main_net_inflow": 500_000,
            "main_net_inflow_pct": 5.0,
            "turnover": 8_000_000,
            "change_pct": 1.5,
        },
    ]
    monkeypatch.setattr(sector_flows, "_fetch_all_sector_flows", lambda limit=500: flows)

    payload = sector_flows.SectorFlowGroupPreviewPayload(
        groups=[
            sector_flows.SectorFlowGroup(
                id="tech",
                name="科技成长",
                include_items=[
                    sector_flows.SectorFlowGroupSelector(
                        type="industry", code="BK0475", name="半导体"
                    )
                ],
                weight=0.5,
                order=1,
            ),
            sector_flows.SectorFlowGroup(
                id="chip",
                name="芯片链",
                includes=["半导体"],
                order=2,
            ),
            sector_flows.SectorFlowGroup(
                id="empty",
                name="空组",
                include_items=[
                    sector_flows.SectorFlowGroupSelector(
                        type="concept", code="BAD", name="不存在"
                    )
                ],
                order=3,
            ),
            sector_flows.SectorFlowGroup(
                id="disabled",
                name="停用组",
                enabled=False,
                include_items=[
                    sector_flows.SectorFlowGroupSelector(
                        type="industry", code="BK0001", name="软件开发"
                    )
                ],
                order=4,
            ),
        ]
    )

    preview = sector_flows.preview_sector_flow_groups(payload)

    assert preview["source"] == "live"
    tech = preview["groups"][0]
    assert tech["id"] == "tech"
    assert tech["raw_main_net_inflow"] == 1_000_000
    assert tech["adjusted_main_net_inflow"] == 500_000
    assert all(group["id"] != "disabled" for group in preview["groups"])
    assert preview["diagnostics"]["duplicate_members"][0]["code"] == "BK0475"
    assert preview["diagnostics"]["empty_groups"][0]["id"] == "empty"
    assert preview["diagnostics"]["invalid_selectors"][0]["group_id"] == "empty"


def test_sector_flow_summary_persists_live_snapshot(monkeypatch, tmp_path):
    """交易时段实时汇总成功后应顺手固化当天快照。"""
    from src.web.api import sector_flows

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sector_flows, "_today_str", lambda: "2026-07-15")
    monkeypatch.setattr(sector_flows, "_is_cn_trading_time", lambda: True)
    monkeypatch.setattr(
        sector_flows,
        "_fetch_all_sector_flows",
        lambda limit=500: [
            {
                "code": "BK0475",
                "name": "半导体",
                "type": "industry",
                "main_net_inflow": 1_000_000,
                "main_net_inflow_pct": 10.0,
                "turnover": 10_000_000,
                "change_pct": 2.5,
            }
        ],
    )
    sector_flows.update_sector_flow_groups(
        sector_flows.SectorFlowGroupsPayload(
            groups=[
                sector_flows.SectorFlowGroup(name="科技成长", includes=["半导体"])
            ]
        )
    )

    summary = sector_flows.get_sector_flow_group_summary()

    assert summary["source"] == "live"
    assert summary["groups"][0]["main_net_inflow"] == 1_000_000
    assert Path(tmp_path, "sector_flow_snapshots", "2026-07-15.json").exists()


def test_sector_flow_summary_uses_snapshot_after_close(monkeypatch, tmp_path):
    """非交易时段已有当天快照时不应再强制实时查询。"""
    from src.web.api import sector_flows

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sector_flows, "_today_str", lambda: "2026-07-15")
    monkeypatch.setattr(sector_flows, "_is_cn_trading_time", lambda: False)
    sector_flows.update_sector_flow_groups(
        sector_flows.SectorFlowGroupsPayload(
            groups=[
                sector_flows.SectorFlowGroup(name="科技成长", includes=["半导体"])
            ]
        )
    )
    sector_flows._save_snapshot(
        "2026-07-15",
        [
            {
                "code": "BK0475",
                "name": "半导体",
                "type": "industry",
                "main_net_inflow": 2_000_000,
                "main_net_inflow_pct": 20.0,
                "turnover": 10_000_000,
                "change_pct": 2.5,
            }
        ],
    )
    monkeypatch.setattr(
        sector_flows,
        "_fetch_all_sector_flows",
        lambda limit=500: (_ for _ in ()).throw(AssertionError("should not fetch live")),
    )

    summary = sector_flows.get_sector_flow_group_summary()

    assert summary["source"] == "snapshot"
    assert summary["groups"][0]["main_net_inflow"] == 2_000_000


def test_sector_flow_summary_falls_back_when_live_empty(monkeypatch, tmp_path):
    """实时源返回空时应回退今天或最近快照，避免页面显示 0 组。"""
    from src.web.api import sector_flows

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sector_flows, "_today_str", lambda: "2026-07-15")
    monkeypatch.setattr(sector_flows, "_is_cn_trading_time", lambda: True)
    monkeypatch.setattr(sector_flows, "_fetch_all_sector_flows", lambda limit=500: [])
    sector_flows.update_sector_flow_groups(
        sector_flows.SectorFlowGroupsPayload(
            groups=[
                sector_flows.SectorFlowGroup(name="医药健康", includes=["医药"])
            ]
        )
    )
    sector_flows._save_snapshot(
        "2026-07-15",
        [
            {
                "code": "BK1216",
                "name": "医药生物",
                "type": "industry",
                "main_net_inflow": 3_000_000,
                "main_net_inflow_pct": 6.0,
                "turnover": 50_000_000,
                "change_pct": 3.1,
            }
        ],
    )

    summary = sector_flows.get_sector_flow_group_summary()

    assert summary["source"] == "snapshot"
    assert summary["groups"][0]["name"] == "医药健康"
    assert summary["groups"][0]["main_net_inflow"] == 3_000_000


def test_list_sectors_falls_back_to_snapshot_when_live_empty(monkeypatch, tmp_path):
    """板块搜索实时源为空时应回退本地快照，保证前端配置搜索可用。"""
    from src.web.api import sector_flows

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sector_flows, "_today_str", lambda: "2026-07-15")
    monkeypatch.setattr(
        sector_flow_collector.EastMoneySectorFlowCollector,
        "fetch_sectors",
        lambda self, sector_type="industry", keyword="", limit=500: [],
    )
    sector_flows._save_snapshot(
        "2026-07-15",
        [
            {
                "code": "BK0475",
                "name": "半导体",
                "type": "industry",
                "main_net_inflow": 1_000_000,
                "turnover": 10_000_000,
            },
            {
                "code": "BK1106",
                "name": "创新药",
                "type": "concept",
                "main_net_inflow": 2_000_000,
                "turnover": 20_000_000,
            },
        ],
    )

    industry = sector_flows.list_sectors(type="industry", keyword="半导", limit=10)
    concept = sector_flows.list_sectors(type="concept", keyword="创新", limit=10)

    assert industry["items"][0]["name"] == "半导体"
    assert industry["items"][0]["source"] == "snapshot"
    assert concept["items"][0]["name"] == "创新药"


def test_sector_flow_groups_fallback_to_default_config(monkeypatch, tmp_path):
    """未写入 DATA_DIR 配置时应回退读取 config/sector_flow_groups.json。"""
    from src.web.api import sector_flows

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    res = sector_flows.get_sector_flow_groups()
    assert res["groups"]
    assert any(group["name"] == "科技成长" for group in res["groups"])
    defaults = sector_flows.get_default_sector_flow_groups()
    assert defaults["groups"]
    assert defaults["groups"][0]["include_items"]


def test_sector_flow_routers_mounted():
    """板块资金流相关接口已挂载到 app。"""
    from src.web.app import app

    paths = set(app.openapi().get("paths", {}).keys())
    assert "/api/sector-flows" in paths
    assert "/api/sector-flows/{board_code}/history" in paths
    assert "/api/sector-flows/{board_code}/stocks" in paths
    assert "/api/sector-flows/snapshots/refresh" in paths
    assert "/api/sector-flows/snapshots" in paths
    assert "/api/sectors" in paths
    assert "/api/sector-flow-groups" in paths
    assert "/api/sector-flow-groups/defaults" in paths
    assert "/api/sector-flow-groups/preview" in paths
    assert "/api/sector-flow-groups/summary" in paths
