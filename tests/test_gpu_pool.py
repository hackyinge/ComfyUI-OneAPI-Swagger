from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
import weakref
from unittest.mock import patch


GIB = 1024 ** 3


class NativeModel:
    def __init__(self, memory):
        self.memory = memory
        self.load_device = memory.device

    def loaded_size(self):
        return self.memory.native


class Memory:
    LoadedModel = staticmethod(lambda model: model)
    def __init__(self):
        self.free = 16 * GIB
        self.native = 0
        self.unloads = []
        self.device = SimpleNamespace(type='cuda')
        self.model = NativeModel(self)

    def get_torch_device(self):
        return self.device

    def get_total_memory(self, device):
        return 24 * GIB

    def get_free_memory(self, device):
        return self.free

    def extra_reserved_memory(self):
        return 1.5 * GIB

    def loaded_models(self):
        return [self.model] if self.native else []

    def free_memory(self, required, device, keep_loaded=()):
        freed = 0 if self.model in keep_loaded else min(self.native, max(0, required - self.free))
        self.native -= freed
        self.free += freed
        self.unloads.append(freed)


class GPUPoolTests(unittest.TestCase):
    def setUp(self):
        self.memory = Memory()
        self.temp = tempfile.TemporaryDirectory()
        self.model = Path(self.temp.name) / 'weights'
        with self.model.open('wb') as file:
            file.truncate(2 * GIB)
        folders = SimpleNamespace(get_full_path=lambda group, name: str(self.model))
        spec = spec_from_file_location('oneapi_pool_test', Path(__file__).parents[1] / 'gpu_pool.py')
        self.module = module_from_spec(spec)
        with patch.dict(sys.modules, {'folder_paths': folders, 'comfy': SimpleNamespace(model_management=self.memory), 'comfy.model_management': self.memory}):
            spec.loader.exec_module(self.module)
        self.pool = self.module.GPUPool()
        self.released = []

    def tearDown(self):
        self.temp.cleanup()

    def resource(self, name, models, size, age=1, device='cuda'):
        state = {'models': models, 'device': device, 'vram_bytes': size}
        holder = [state]
        def release():
            self.released.append(name)
            self.memory.free += holder[0]['vram_bytes']
            holder[0] = None
        self.pool.register(name, lambda: holder[0], release)
        self.pool.last_used[name] = age
        return holder

    def speech(self, model='qwen3-tts-1.7b', device='cuda'):
        return {'speech': {'class_type': 'OneAPIQwenSpeech', 'inputs': {'model': model, 'device': device}}}

    def image(self):
        return {'model': {'class_type': 'CheckpointLoaderSimple', 'inputs': {'ckpt_name': 'image.safetensors'}}}

    def test_small_image_keeps_speech_when_capacity_is_sufficient(self):
        self.resource('tts', ['qwen3-tts-1.7b'], 5 * GIB)
        self.resource('asr', ['qwen3-asr-0.6b'], 2 * GIB)
        decision = self.pool.prepare(self.image())
        self.assertEqual(decision['action'], 'reuse_or_keep')
        self.assertEqual(self.released, [])

    def test_repeated_tts_does_not_count_its_weights_twice(self):
        self.memory.free = 3 * GIB
        self.resource('tts', ['qwen3-tts-1.7b'], 5 * GIB)
        decision = self.pool.prepare(self.speech())
        self.assertEqual(decision['reusable_bytes'], 5 * GIB)
        self.assertTrue(decision['budget_satisfied'])
        self.assertEqual(self.released, [])

    def test_lru_partially_offloads_native_models_before_newer_speech(self):
        self.memory.free, self.memory.native = GIB, 15 * GIB
        self.pool.last_used['native'] = 0
        self.resource('tts', ['qwen3-tts-1.7b'], 5 * GIB, age=2)
        self.resource('asr', ['qwen3-asr-0.6b'], 2 * GIB, age=1)
        decision = self.pool.prepare(self.speech())
        self.assertEqual([item['resource'] for item in decision['evictions']], ['native'])
        self.assertEqual(self.memory.unloads, [1.5 * GIB])
        self.assertEqual(self.released, [])

    def test_different_tts_model_can_evict_old_slot_before_other_resources(self):
        self.memory.free = GIB
        self.resource('tts', ['qwen3-tts-1.7b'], 5 * GIB, age=0)
        self.resource('asr', ['qwen3-asr-0.6b'], 2 * GIB, age=1)
        decision = self.pool.prepare(self.speech('qwen3-tts-clone-0.6b'))
        self.assertTrue(decision['budget_satisfied'])
        self.assertEqual(self.released, ['tts'])

    def test_same_native_assets_reuse_loaded_weights(self):
        self.memory.free, self.memory.native = 3 * GIB, 3.5 * GIB
        self.pool.native_groups[(('checkpoints', 'image.safetensors'),)] = {'refs': [weakref.ref(self.memory.model)], 'last_used': 1}
        self.resource('tts', ['qwen3-tts-1.7b'], 5 * GIB)
        decision = self.pool.prepare(self.image())
        self.assertGreater(decision['reusable_bytes'], 0)
        self.assertEqual(decision['evictions'], [])

    def test_other_native_models_are_not_credited_as_reusable_and_can_be_evicted(self):
        first = NativeModel(SimpleNamespace(device=self.memory.device, native=2 * GIB))
        other = NativeModel(SimpleNamespace(device=self.memory.device, native=8 * GIB))
        self.memory.loaded_models = lambda: [first, other]
        self.memory.free = GIB
        self.pool.native_groups[(('checkpoints', 'image.safetensors'),)] = {'refs': [weakref.ref(first)], 'last_used': 2}
        self.pool.native_groups[(('checkpoints', 'other.safetensors'),)] = {'refs': [weakref.ref(other)], 'last_used': 0}
        self.resource('tts', ['qwen3-tts-1.7b'], 5 * GIB, age=1)
        def unload(required, device, keep_loaded=()):
            self.assertIn(first, keep_loaded)
            self.assertNotIn(other, keep_loaded)
            freed = min(other.loaded_size(), required - self.memory.free)
            other.memory.native -= freed
            self.memory.free += freed
        self.memory.free_memory = unload
        decision = self.pool.prepare(self.image())
        self.assertEqual(decision['reusable_bytes'], 2 * GIB)
        self.assertEqual([item['resource'] for item in decision['evictions']], ['native'])
        self.assertEqual(self.released, [])

    def test_unknown_linked_loader_uses_conservative_budget(self):
        self.memory.free = 18.5 * GIB
        self.resource('tts', ['qwen3-tts-1.7b'], 5 * GIB)
        decision = self.pool.prepare({'model': {'class_type': 'UNETLoader', 'inputs': {'unet_name': ['other', 0]}}})
        self.assertEqual(self.released, ['tts'])
        self.assertTrue(decision['budget_satisfied'])

    def test_cpu_job_does_not_evict_gpu_models(self):
        self.memory.free = GIB
        self.resource('tts', ['qwen3-tts-1.7b'], 5 * GIB)
        decision = self.pool.prepare(self.speech(device='cpu'))
        self.assertEqual(decision['action'], 'no_gpu_required')
        self.assertEqual(self.released, [])

    def test_idle_reclaims_gpu_but_keeps_cpu_model_and_does_not_spin(self):
        self.resource('tts', ['qwen3-tts-1.7b'], 5 * GIB)
        self.resource('asr', ['qwen3-asr-0.6b'], 0, device='cpu')
        self.memory.native = GIB
        self.pool.last_activity = time.monotonic() - 121
        self.pool.collect_idle()
        self.assertEqual(self.released, ['tts'])
        self.assertEqual(self.memory.native, 0)
        self.assertLess(time.monotonic() - self.pool.last_activity, 1)

    def test_queue_prepares_after_dequeue_and_preserves_completion_contract(self):
        item = (0, 'job-1', self.speech(), {'user_id': 'alice'}, ['save'])
        calls = []
        queue = SimpleNamespace(mutex=threading.RLock(), not_empty=threading.Condition(), currently_running={})
        def get(timeout=None):
            calls.append('dequeue')
            queue.currently_running[7] = item
            return item, 7
        def done(item_id, history_result, **kwargs):
            calls.append(('done', item_id, history_result, kwargs))
            return 'completed'
        queue.get, queue.task_done = get, done
        self.pool.install(queue)
        self.assertEqual(queue.get(timeout=1000), (item, 7))
        self.assertIn('job-1', self.pool.decisions)
        self.assertEqual(queue.task_done(7, {'outputs': {}}, status='ok'), 'completed')
        self.assertEqual(calls, ['dequeue', ('done', 7, {'outputs': {}}, {'status': 'ok'})])

    def test_status_filters_decisions_by_owner(self):
        self.pool.decisions['one'] = {'job_id': 'one', 'owner': 'alice'}
        self.pool.decisions['two'] = {'job_id': 'two', 'owner': 'bob'}
        status = self.pool.status('alice')
        self.assertEqual(status['recent'], [{'job_id': 'one'}])


if __name__ == '__main__':
    unittest.main()
