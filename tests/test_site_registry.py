import unittest

from app.site_registry import get_site_config


class SiteRegistryTests(unittest.TestCase):
    def test_default_site_config_aggregates_all_providers(self) -> None:
        site = get_site_config("default")
        self.assertEqual(site.site_id, "default")
        self.assertEqual(
            site.provider_ids,
            (
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
        )
        self.assertEqual(site.release_tag_prefix, "default-bundles")
        self.assertEqual(site.release_shard_size, 900)
        self.assertEqual(site.public_min_years, 2)
        self.assertEqual(
            {
                key: value for key, value in site.public_min_years_by_canonical_prefix.items()
                if not key.startswith("wdasec-")
            },
            {
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
                "ipas-cert": 1,
                "hce-cmu": 1,
                "hce-tcu": 1,
                "hce-nsysu": 1,
                "hce-nthu": 1,
                "post-recruit": 1,
            },
        )
        self.assertEqual(site.required_provider_ids, ("moex", "ceec_gsat"))
        wda_exceptions = {
            key: value for key, value in site.public_min_years_by_canonical_prefix.items()
            if key.startswith("wdasec-")
        }
        self.assertEqual(len(wda_exceptions), 9)
        self.assertTrue(all(
            key.endswith("-paper-form-foreign-assistance-master") and value == 1
            for key, value in wda_exceptions.items()
        ))

    def test_default_site_includes_financial_cert_providers(self) -> None:
        config = get_site_config("default")
        for provider_id in ("sfi_cert", "tabf_cert", "tii_cert"):
            with self.subTest(provider_id=provider_id):
                self.assertIn(provider_id, config.provider_ids)

    def test_default_site_includes_requested_topic_providers(self) -> None:
        config = get_site_config("default")
        for provider_id in (
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
        ):
            with self.subTest(provider_id=provider_id):
                self.assertIn(provider_id, config.provider_ids)
