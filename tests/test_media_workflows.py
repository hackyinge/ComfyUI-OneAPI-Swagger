from pathlib import Path
import sys
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).parents[1]))
from media_workflows import build_media_workflow


class MediaWorkflowTests(unittest.TestCase):
    def test_video_retains_existing_default(self):
        graph = build_media_workflow({'task': 'video', 'prompt': 'test'})
        self.assertEqual((graph['condition']['inputs']['width'], graph['condition']['inputs']['height']), (896, 512))
        self.assertEqual(graph['refine_decode']['class_type'], 'VAEDecodeTiled')

    def test_new_image_defaults_use_native_conditioning_and_sampling(self):
        graph = build_media_workflow({'width': 768, 'height': 512, 'n': 2})
        self.assertEqual(graph['condition']['class_type'], 'TextEncodeQwenImage21')
        self.assertEqual(graph['latent']['inputs'], {'width': 768, 'height': 512, 'batch_size': 2})
        self.assertEqual(graph['model']['inputs']['unet_name'], 'qwen_image_2.1_turbo_int8_convrot.safetensors')
        self.assertEqual(graph['sample']['inputs']['guider'], ['guider', 0])
        self.assertEqual(graph['guider']['inputs']['cfg'], 1.0)
        self.assertNotIn('sampling', graph)

    def test_image_requests_randomize_omitted_null_and_minus_one_seeds(self):
        for model in ('qwen-image-2.1', 'qwen-image-2.1-turbo'):
            for extra in ({}, {'seed': None}, {'seed': -1}):
                with self.subTest(model=model, extra=extra), patch('media_workflows.secrets.randbits', side_effect=[123, 456]) as rng:
                    graphs = [build_media_workflow({'model': model, **extra}) for _ in range(2)]
                    key, field = ('sample', 'seed') if model == 'qwen-image-2.1' else ('noise', 'noise_seed')
                    self.assertEqual([g[key]['inputs'][field] for g in graphs], [123, 456])
                    self.assertEqual(rng.call_count, 2)

    def test_explicit_image_seeds_including_zero_are_kept_without_rng(self):
        for model in ('qwen-image-2.1', 'qwen-image-2.1-turbo'):
            for seed in (0, 42):
                with self.subTest(model=model, seed=seed), patch('media_workflows.secrets.randbits') as rng:
                    graph = build_media_workflow({'model': model, 'seed': seed})
                    key, field = ('sample', 'seed') if model == 'qwen-image-2.1' else ('noise', 'noise_seed')
                    self.assertEqual(graph[key]['inputs'][field], seed)
                    rng.assert_not_called()

    def test_edit_uses_reference_conditioning_and_native_latent(self):
        for model, kind in [('qwen-image-2.1', 'TextEncodeQwenImage21'), ('qwen-image-2.1-turbo', 'TextEncodeQwenImage21')]:
            graph = build_media_workflow({'model': model, 'input_image': 'photo.png', 'n': 3})
            self.assertEqual(graph['condition']['class_type'], kind)
            self.assertEqual(graph['condition']['inputs']['images.image_1'], ['resize', 0])
            self.assertEqual(graph['condition']['inputs']['negative_prompt'], '')
            self.assertEqual(graph['batch']['inputs']['samples'], ['condition', 2])
            self.assertEqual(graph['batch']['inputs']['amount'], 3)

    def test_qwen_turbo_loads_distilled_checkpoint_and_official_eight_step_schedule(self):
        graph = build_media_workflow({'model': 'qwen-image-2.1-turbo', 'seed': 7})
        self.assertEqual(graph['model']['inputs']['unet_name'], 'qwen_image_2.1_turbo_int8_convrot.safetensors')
        self.assertNotIn('turbo_lora', graph)
        self.assertEqual(graph['guider']['inputs']['model'], ['model', 0])
        self.assertEqual(graph['noise']['inputs']['noise_seed'], 7)
        self.assertEqual(graph['sample']['class_type'], 'SamplerCustomAdvanced')
        self.assertEqual([float(x) for x in graph['sigmas']['inputs']['sigmas'].split(',')],
                         [1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0.0])

    def test_turbo_lower_steps_cover_full_denoising_and_partial_denoise_remains_available(self):
        for steps in (4, 6):
            graph = build_media_workflow({'model': 'qwen-image-2.1-turbo', 'steps': steps})
            sigmas = [float(x) for x in graph['sigmas']['inputs']['sigmas'].split(',')]
            self.assertEqual(len(sigmas), steps + 1)
            self.assertEqual((sigmas[0], sigmas[-1]), (1.0, 0.0))
            self.assertTrue(all(a > b for a, b in zip(sigmas, sigmas[1:])))
        graph = build_media_workflow({'model': 'qwen-image-2.1-turbo', 'denoise': 0.5})
        self.assertEqual(graph['denoise']['inputs']['denoise'], 0.5)
        self.assertEqual(graph['sample']['inputs']['sigmas'], ['denoise', 1])
        with self.assertRaises(ValueError):
            build_media_workflow({'model': 'fibo-2.0'})

    def test_multiple_reference_images_preserve_order_and_independent_output_batch(self):
        for model in ('qwen-image-2.1', 'qwen-image-2.1-turbo'):
            for count in (1, 2, 3):
                files = [f'reference_{i}.png' for i in range(count)]
                graph = build_media_workflow({'model': model, 'input_images': files, 'n': 2})
                self.assertEqual(graph['source']['inputs']['image'], files[0])
                self.assertEqual(graph['condition']['inputs']['images.image_1'], ['resize', 0])
                for i in range(2, count + 1):
                    self.assertEqual(graph[f'source_{i}']['inputs']['image'], files[i - 1])
                    self.assertEqual(graph['condition']['inputs'][f'images.image_{i}'], [f'reference_size_{i}', 0])
                    self.assertEqual(graph[f'reference_size_{i}']['inputs']['image'], [f'source_{i}', 0])
                self.assertEqual(graph['sample']['inputs']['latent_image'], ['batch', 0])
                self.assertEqual(graph['batch']['inputs']['amount'], 2)
        graph = build_media_workflow({'input_image': 'first.png', 'input_images': ['second.png', 'third.png']})
        self.assertEqual(graph['source']['inputs']['image'], 'first.png')
        self.assertEqual(graph['source_2']['inputs']['image'], 'second.png')
        self.assertEqual(graph['source_3']['inputs']['image'], 'third.png')

    def test_original_reference_pixels_can_be_requested(self):
        graph = build_media_workflow({'input_images': ['one.png', 'two.png'], 'reference_megapixels': 0})
        self.assertNotIn('reference_size_2', graph)
        self.assertEqual(graph['condition']['inputs']['images.image_2'], ['source_2', 0])

    def test_reference_list_shape_and_each_filename_are_validated(self):
        for value in ('not-a-list.png', None, ['ok.png', '../other.png'], ['ok.png', '/etc/passwd'], ['']):
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_media_workflow({'input_images': value})
        self.assertIn('latent', build_media_workflow({'input_images': []}))

    def test_local_audio_is_offline_and_uses_native_save_nodes(self):
        graph = build_media_workflow({'task': 'tts', 'text': 'hello', 'voice': 'lessac'})
        self.assertEqual(graph['speech']['class_type'], 'OneAPIPiperSpeech')
        self.assertEqual(graph['save']['class_type'], 'SaveAudio')
        graph = build_media_workflow({'task': 'asr', 'input_audio': 'voice.wav', 'word_timestamps': True})
        self.assertEqual(graph['transcript_details']['inputs']['text'], ['transcribe', 1])
        self.assertEqual(graph['transcript']['class_type'], 'SaveText')

    def test_input_paths_and_unknown_options_fail_before_queue(self):
        for payload in ({'task': 'asr', 'input_audio': '../other.wav'}, {'input_image': '/etc/passwd'}, {'model': 'invented'}, {'task': 'tts', 'model': 'cloud'}, {'mistyped': 1}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                build_media_workflow(payload)

    def test_voice_clone_uses_uploaded_audio_and_requires_transcript_or_embedding_mode(self):
        settings = {'task': 'tts', 'model': 'qwen3-tts-clone-0.6b', 'text': '新的句子'}
        for extra in ({}, {'reference_audio': 'reference.wav'}, {'reference_audio': '../outside.wav', 'x_vector_only': True}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                build_media_workflow({**settings, **extra})
        graph = build_media_workflow({**settings, 'reference_audio': 'reference.wav', 'reference_text': '参考句子'})
        self.assertEqual(graph['speech']['inputs']['reference_audio'], ['reference', 0])
        self.assertEqual(graph['reference']['class_type'], 'LoadAudio')
        self.assertEqual(graph['speech_details']['inputs']['text'], ['speech', 1])

    def test_qwen_asr_keeps_context_and_word_alignment_on_the_recognition_node(self):
        graph = build_media_workflow({'task': 'asr', 'model': 'qwen3-asr-0.6b', 'input_audio': 'test.wav', 'prompt': '彗星', 'word_timestamps': True})
        self.assertEqual(graph['transcribe']['class_type'], 'OneAPIQwenTranscribe')
        self.assertEqual(graph['transcribe']['inputs']['prompt'], '彗星')
        self.assertTrue(graph['transcribe']['inputs']['word_timestamps'])
        self.assertEqual(graph['transcript_details']['inputs']['text'], ['transcribe', 1])

    def test_qwen_residency_and_cpu_controls_reach_execution(self):
        for task, payload, name in [('tts', {'text': '你好'}, 'speech'), ('asr', {'input_audio': 'voice.wav'}, 'transcribe')]:
            graph = build_media_workflow({'task': task, **payload})
            self.assertTrue(graph[name]['inputs']['keep_loaded'])
            self.assertEqual(graph[name]['inputs']['device'], 'cuda')
            graph = build_media_workflow({'task': task, **payload, 'device': 'cpu', 'keep_loaded': False})
            self.assertFalse(graph[name]['inputs']['keep_loaded'])
            self.assertEqual(graph[name]['inputs']['device'], 'cpu')
        for extra in ({'device': 'other'}, {'keep_loaded': 'false'}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                build_media_workflow({'task': 'tts', 'text': '你好', **extra})

    def test_chinese_defaults_preserve_explicit_piper_voices(self):
        graph = build_media_workflow({'task': 'tts', 'text': '你好'})
        self.assertEqual(graph['speech']['inputs']['model'], 'qwen3-tts-1.7b')
        self.assertEqual(graph['speech']['inputs']['voice'], 'Vivian')
        for voice in ('huayan', 'lessac', 'zh_CN-huayan-medium.onnx'):
            graph = build_media_workflow({'task': 'tts', 'text': '你好', 'voice': voice})
            self.assertEqual(graph['speech']['class_type'], 'OneAPIPiperSpeech')
        graph = build_media_workflow({'task': 'asr', 'input_audio': 'test.wav'})
        self.assertEqual(graph['transcribe']['inputs']['model'], 'qwen3-asr-0.6b')


if __name__ == '__main__':
    unittest.main()
