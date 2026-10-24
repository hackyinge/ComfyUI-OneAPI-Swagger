import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("video_workflows", Path(__file__).parents[1] / "video_workflows.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
build = module.build_workflow


class WorkflowTests(unittest.TestCase):
    def test_default_is_low_resolution_without_refinement_or_postprocessing(self):
        graph = build({"prompt": "test", "seed": 42})
        self.assertEqual((graph["condition"]["inputs"]["width"], graph["condition"]["inputs"]["height"]), (896, 512))
        self.assertEqual(graph["attention"]["class_type"], "MiniMaxH3MemoryEfficientSolAttentionPatch")
        self.assertEqual(graph["decode"]["inputs"]["video_vae"], ["vae", 0])
        for node in ("decode_vae", "refine_sample", "draft_save", "upscale", "denoise", "deblur", "interpolate", "hdr"):
            self.assertNotIn(node, graph)
        self.assertEqual(graph["video"]["inputs"]["fps"], 24)

    def test_explicit_quality_settings_retain_native_refinement(self):
        settings = {"refiner": True, "fast_decode": True, "width": 1344, "height": 768,
                    "draft_width": 896, "draft_height": 512, "seed": 42}
        graph = build(settings)
        self.assertEqual(graph["decode_vae"]["inputs"]["vae_name"], "taeh3.safetensors")
        self.assertEqual(graph["refine_decode"]["class_type"], "VAEDecodeTiled")
        self.assertEqual(graph["refine_decode"]["inputs"]["tile_size"], 768)
        self.assertNotIn("draft_save", graph)
        with_draft = build({**settings, "save_draft": True})
        self.assertIn("draft_save", with_draft)
        self.assertEqual(with_draft["sample"], graph["sample"])

    def test_postprocess_switches_are_independent_and_explicit_false_wins(self):
        for switch, node in (("upscale", "upscale"), ("denoise", "denoise"), ("deblur", "deblur"), ("interpolate", "interpolate"), ("hdr", "hdr")):
            graph = build({"postprocess": {switch: True}})
            self.assertIn(node, graph)
            for other in {"upscale", "denoise", "deblur", "interpolate", "hdr"} - {node}:
                self.assertNotIn(other, graph)
        graph = build({"postprocess": {"interpolate": False, "fps_multiplier": 2}})
        self.assertNotIn("interpolate", graph)
        self.assertEqual(graph["video"]["inputs"]["fps"], 24)
        graph = build({"postprocess": {"interpolate": True}})
        self.assertEqual(graph["video"]["inputs"]["fps"], 48)
        for post in ({}, {key: False for key in ("upscale", "denoise", "deblur", "interpolate", "hdr")}):
            graph = build({"postprocess": post})
            self.assertEqual(graph["video"]["inputs"]["fps"], 24)
            self.assertNotIn("upscale", graph)

    def test_postprocess_typos_and_string_booleans_are_not_silently_applied(self):
        for post in ({"upscal": True}, {"upscale": "false"}, {"interpolate": 1}):
            with self.subTest(post=post), self.assertRaises(ValueError):
                build({"postprocess": post})

    def test_video_seeds_randomize_once_and_are_shared_with_refinement(self):
        for extra in ({}, {"seed": None}, {"seed": -1}):
            with self.subTest(extra=extra), patch.object(module.secrets, 'randbits', side_effect=[123, 456]) as rng:
                graphs = [build({"refiner": True, **extra}) for _ in range(2)]
                self.assertEqual([g['noise']['inputs']['noise_seed'] for g in graphs], [123, 456])
                self.assertEqual([g['refine_noise']['inputs']['noise_seed'] for g in graphs], [123, 456])
                self.assertEqual(rng.call_count, 2)
        graph = build({"refiner": True, "seed": 0})
        self.assertEqual(graph['noise']['inputs']['noise_seed'], 0)
        self.assertEqual(graph['refine_noise']['inputs']['noise_seed'], 0)

    def test_postprocessing_does_not_inherit_refiner_default(self):
        graph = build({"mode": "postprocess", "input_video": "a.mp4", "postprocess": {"deblur": True}})
        self.assertNotIn("refine_sample", graph)
        self.assertEqual(graph["deblur"]["inputs"]["images"], ["source", 0])

    def test_duration_grid_and_audio_trim(self):
        for seconds, length in ((5, 124), (1, 39), (10, 243)):
            graph = build({"seconds": seconds, "seed": 42})
            self.assertEqual(graph["condition"]["inputs"]["length"], length)
            self.assertEqual(graph["trim"]["inputs"]["duration_seconds"], seconds)
            self.assertEqual(graph["video"]["inputs"]["audio"], ["trim", 1])

    def test_reference_model_and_lora_match(self):
        graph = build({"mode": "ref2v", "ref_images": ["ref/a.png", "b.png"]})
        self.assertIn("ref2va", graph["model"]["inputs"]["unet_name"])
        self.assertIn("ref2v_turbo", graph["lora"]["inputs"]["lora_name"])
        self.assertEqual(graph["condition"]["inputs"]["ref_images.ref_image_1"], ["ref_image_1", 0])

    def test_vdn_does_not_stack_turbo_or_sparse_attention(self):
        graph = build({"profile": "vdn4"})
        self.assertNotIn("pruned", graph["model"]["inputs"]["unet_name"])
        self.assertNotIn("lora", graph)
        self.assertEqual(graph["attention"]["class_type"], "VDNH3Accelerator")

    def test_audio_lock_uses_source_for_mux(self):
        graph = build({"mode": "audio2v", "drive_audio": "voice.wav"})
        self.assertEqual(graph["trim"]["inputs"]["audio"], ["condition", 2])
        self.assertEqual(graph["condition"]["inputs"]["audio_mode"], "lock_source")

    def test_fast_decode_does_not_change_conditioning_encoder(self):
        graph = build({"fast_decode": True, "first_frame": "a.png"})
        self.assertEqual(graph["condition"]["inputs"]["video_vae"], ["vae", 0])
        self.assertEqual(graph["decode"]["inputs"]["video_vae"], ["decode_vae", 0])
        self.assertEqual(graph["decode_vae"]["inputs"]["vae_name"], "taeh3.safetensors")

    def test_keyframes_and_audio_do_not_use_disabled_hybrid(self):
        graph = build({"mode": "fl2v", "first_frame": "a.png", "last_frame": "b.png", "drive_audio": "voice.wav"})
        self.assertFalse(graph["condition"]["inputs"]["add_source_as_reference"])

    def test_references_and_keyframes_use_native_guides(self):
        graph = build({"mode": "ref2v", "ref_images": ["a.png"], "first_frame": "b.png", "drive_audio": "voice.wav"})
        self.assertEqual(graph["condition"]["class_type"], "MiniMaxH3ReferenceToVideo")
        self.assertEqual(graph["guider"]["inputs"]["conditioning"], ["guide_first_frame", 0])
        self.assertEqual(graph["sample"]["inputs"]["latent_image"], ["audio_control", 0])
        self.assertEqual(graph["trim"]["inputs"]["audio"], ["drive_audio", 0])

    def test_interpolation_preserves_duration_and_hdr_metadata(self):
        graph = build({"postprocess": {"fps_multiplier": 2, "hdr": True}})
        self.assertEqual(graph["video"]["inputs"]["fps"], 48)
        self.assertEqual(graph["last_image"]["inputs"]["batch_index"], -1)
        self.assertEqual(graph["hold_last"]["inputs"]["amount"], 1)
        self.assertEqual(graph["video"]["inputs"]["color_space"], "HDR PQ")
        self.assertEqual(graph["video"]["inputs"]["bit_depth"], 10)

    def test_refiner_preserves_h3_audio_and_final_frame_count(self):
        graph = build({"refiner": True, "width": 1152, "height": 640, "seconds": 5, "seed": 42})
        self.assertEqual((graph["condition"]["inputs"]["width"], graph["condition"]["inputs"]["height"]), (576, 320))
        self.assertEqual(graph["refine_padding"]["inputs"]["amount"], 1)
        self.assertEqual(graph["refine_sigmas"]["inputs"]["sigmas"], "0.909375, 0")
        self.assertEqual(graph["video"]["inputs"]["audio"], ["trim", 1])
        self.assertEqual(graph["refine_trim"]["inputs"]["length"], 120)
        self.assertEqual(graph["video"]["inputs"]["images"], ["refine_trim", 0])

    def test_official_geometry_keeps_draft_and_refiner_sizes_separate(self):
        graph = build({"refiner": True, "save_draft": True, "draft_width": 896, "draft_height": 512, "width": 1344, "height": 768})
        self.assertEqual((graph["condition"]["inputs"]["width"], graph["condition"]["inputs"]["height"]), (896, 512))
        self.assertEqual((graph["refine_resize"]["inputs"]["width"], graph["refine_resize"]["inputs"]["height"]), (672, 384))
        self.assertEqual(graph["draft_save"]["inputs"]["video"], ["draft_video", 0])

    def test_uploaded_video_refinement_does_not_generate_h3(self):
        graph = build({"mode": "refine", "input_video": "draft.mp4", "seed": 42})
        self.assertNotIn("condition", graph)
        self.assertEqual(graph["video"]["inputs"]["audio"], ["source", 2])
        with self.assertRaises(ValueError):
            build({"mode": "refine", "input_video": "../draft.mp4"})

    def test_fusion_composes_after_attention_without_changing_conditioning(self):
        graph = build({"profile": "triton4", "fused_modulation": True})
        self.assertEqual(graph["fusion"]["inputs"]["model"], ["attention", 0])
        self.assertEqual(graph["clock"]["inputs"]["model"], ["fusion", 0])

    def test_tae_refiner_changes_only_final_decoder(self):
        graph = build({"refiner": True, "refiner_decoder": "tae", "width": 1344, "height": 768, "draft_width": 896, "draft_height": 512})
        self.assertEqual(graph["refine_encode"]["inputs"]["vae"], ["refine_vae", 0])
        self.assertEqual(graph["refine_upscale"]["inputs"]["vae"], ["refine_vae", 0])
        self.assertEqual(graph["refine_decode"]["class_type"], "VAEDecode")
        self.assertEqual(graph["refine_decode"]["inputs"]["vae"], ["refine_decode_vae", 0])
        self.assertEqual(graph["refine_trim"]["inputs"]["length"], 120)

    def test_upload_path_boundary(self):
        for name in ("../secret.png", "/etc/passwd", "C:\\test.png", "x/../../a.png", "a.png [output]", "x\x00.png"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                build({"first_frame": name})

    def test_missing_required_media(self):
        for mode in ("i2v", "fl2v", "audio2v", "postprocess", "refine"):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                build({"mode": mode})


if __name__ == "__main__":
    unittest.main()
