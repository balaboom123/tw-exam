from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SiteConfig:
    site_id: str
    provider_ids: tuple[str, ...]
    release_tag_prefix: str
    release_shard_size: int = 900
    public_min_years: int = 1
    public_min_years_by_canonical_prefix: dict[str, int] | None = None
    required_provider_ids: tuple[str, ...] = ()
    retain_legacy_asset_names: bool = True


_SITES = {
    "default": SiteConfig(
        site_id="default",
        provider_ids=(
            "moex",
            "ceec_gsat",
            "ceec_ast",
            "tcte_tve",
            "special_admission",
            "post_recruit",
            "hce_cmu",
            "hce_tcu",
            "hce_nsysu",
            "hce_nthu",
            "cpc_recruit",
            "moea_recruit",
            "taipower_recruit",
            "taisugar_recruit",
            "twc_recruit",
            "rcpet_cap",
            "wdasec_skill",
            "sfi_cert",
            "tabf_cert",
            "tii_cert",
            "teacher_qual",
            "teacher_recruit_newtaipei",
            "teacher_recruit_taoyuan_elementary",
            "teacher_recruit_kaohsiung",
            "teacher_recruit_central_alliance",
            "teacher_recruit_taipei_junior",
            "teacher_recruit_taipei_elementary",
            "teacher_recruit_tainan",
            "gept_cert",
            "jlpt_cert",
            "tocfl_cert",
            "hakka_cert",
            "taigi_cert",
            "tqc_cert",
            "ipas_cert",
        ),
        release_tag_prefix="default-bundles",
        release_shard_size=900,
        retain_legacy_asset_names=False,
        public_min_years=2,
        public_min_years_by_canonical_prefix={
            "sfi-": 1,
            "tabf-": 1,
            "tii-": 1,
            "teacher-qual": 1,
            "teacher-recruit-newtaipei": 1,
            "teacher-recruit-taoyuan-elementary": 1,
            "teacher-recruit-kaohsiung": 1,
            "teacher-recruit-central-alliance": 1,
            "teacher-recruit-taipei-junior": 1,
            "teacher-recruit-taipei-elementary": 1,
            "teacher-recruit-tainan": 1,
            "gept-cert": 1,
            "jlpt-cert": 1,
            "tocfl-cert": 1,
            "hakka-cert": 1,
            "taigi-cert": 1,
            "tqc-cert": 1,
            "ipas-cert-ise": 1,
            "ipas-cert-oia": 1,
            "ipas-cert-aiap": 1,
            "ipas-cert-aiot": 1,
            "ipas-cert": 1,
            "hce-cmu": 1,
            "hce-tcu": 1,
            "hce-nthu": 1,
        },
        required_provider_ids=("moex", "ceec_gsat"),
    )
}


def get_site_config(site_id: str) -> SiteConfig:
    try:
        return _SITES[site_id]
    except KeyError as exc:
        raise ValueError(f"Unknown site_id: {site_id}") from exc
