from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1]))
from image_inputs import normalize_image_content


class ImageInputTests(unittest.TestCase):
    def test_ordered_text_and_image_bindings_resolve_named_references(self):
        data, refs = normalize_image_content({'content': [
            {'type': 'text', 'text': '人物参考这张图'},
            {'type': 'image_url', 'id': 'person', 'image_url': {'url': 'person.png'}},
            {'type': 'text', 'text': '衣服用第二张'},
            {'type': 'image', 'id': 'clothes', 'image': 'clothes.png'},
            {'type': 'text', 'text': '背景用第三张'},
            {'type': 'input_image', 'id': 'scene', 'image_url': 'scene.png'},
            {'type': 'text', 'text': '合成<ref:person>的人物、<ref:clothes>的衣服和<ref:scene>的背景'},
        ]})
        self.assertEqual(data['input_images'], ['person.png', 'clothes.png', 'scene.png'])
        self.assertEqual([r['placeholder'] for r in refs], ['<image1>', '<image2>', '<image3>'])
        self.assertLess(data['prompt'].index('人物参考'), data['prompt'].index('[参考图1'))
        self.assertIn('合成<image1>的人物、<image2>的衣服和<image3>的背景', data['prompt'])

    def test_single_file_prompt_and_filename_lists_remain_compatible(self):
        data, refs = normalize_image_content({'input_image': 'first.png', 'prompt': '改成红色'})
        self.assertEqual(data['prompt'], '改成红色')
        self.assertEqual(refs[0]['filename'], 'first.png')
        data, refs = normalize_image_content({'input_image': 'first.png', 'input_images': ['second.png', {'id': 'background', 'image': 'third.png'}], 'prompt': '用<ref:background>的背景'})
        self.assertEqual(data['input_images'], ['first.png', 'second.png', 'third.png'])
        self.assertIn('用<image3>的背景', data['prompt'])

    def test_last_user_message_is_the_current_reference_scope(self):
        data, refs = normalize_image_content({'messages': [
            {'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': 'old.png'}}]},
            {'role': 'assistant', 'content': '旧结果'},
            {'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': 'new.png'}}, {'type': 'text', 'text': '参考这张'}]},
        ]})
        self.assertEqual(data['input_images'], ['new.png'])
        self.assertNotIn('旧结果', data['prompt'])

    def test_repeat_reference_does_not_renumber_or_duplicate_images(self):
        data, refs = normalize_image_content({'content': [{'type': 'image', 'id': 'face', 'image': 'face.png'}, {'type': 'text', 'text': '保留人物'}, {'type': 'image_ref', 'id': 'face'}]})
        self.assertEqual(len(refs), 1)
        self.assertEqual(data['input_images'], ['face.png'])
        self.assertTrue(data['prompt'].endswith('<image1>'))

    def test_ambiguous_or_missing_reference_ids_fail_before_execution(self):
        for data in [
            {'content': [{'type': 'image', 'id': 'a', 'image': 'a.png'}, {'type': 'image', 'id': 'a', 'image': 'b.png'}]},
            {'content': [{'type': 'image_ref', 'id': 'missing'}]},
            {'prompt': '用<ref:missing>的衣服'},
            {'messages': [{'role': 'assistant', 'content': '没有用户输入'}]},
            {'content': [{'type': 'audio', 'audio': 'wrong.wav'}]},
        ]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                normalize_image_content(data)

    def test_nine_or_more_references_are_not_silently_dropped(self):
        for count in (9, 16, 17):
            data, refs = normalize_image_content({'input_images': [f'{i}.png' for i in range(count)]})
            self.assertEqual(len(refs), count)
            self.assertEqual(data['input_images'][-1], f'{count - 1}.png')
            self.assertEqual(refs[-1]['placeholder'], f'<image{count}>')
