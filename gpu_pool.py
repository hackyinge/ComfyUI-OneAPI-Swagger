"""GPU admission and idle eviction at the native queue boundary."""
from collections import OrderedDict
import logging
from pathlib import Path
import threading
import time
import weakref

import folder_paths
import comfy.model_management as memory


GIB = 1024 ** 3
LOADERS = {
    'CheckpointLoaderSimple': [('ckpt_name', 'checkpoints')],
    'UNETLoader': [('unet_name', 'diffusion_models')],
    'CLIPLoader': [('clip_name', 'text_encoders')],
    'DualCLIPLoader': [('clip_name1', 'text_encoders'), ('clip_name2', 'text_encoders')],
    'TripleCLIPLoader': [('clip_name1', 'text_encoders'), ('clip_name2', 'text_encoders'), ('clip_name3', 'text_encoders')],
    'VAELoader': [('vae_name', 'vae')],
    'LoraLoader': [('lora_name', 'loras')],
    'LoraLoaderModelOnly': [('lora_name', 'loras')],
    'LatentUpscaleModelLoader': [('model_name', 'latent_upscale_models')],
}
TTS_WORKING = {'qwen3-tts-1.7b': 6 * GIB, 'qwen3-tts-0.6b': 4 * GIB, 'qwen3-tts-clone-0.6b': 4.5 * GIB}
ASR_WORKING = {'qwen3-asr-0.6b': 2 * GIB, 'qwen3-asr-1.7b': 4.5 * GIB}


def graph_requirements(graph):
    assets, speech, kinds = set(), {}, set()
    unknown = False
    pixels, frames, batch = 512 * 512, 1, 1
    for node in graph.values():
        kind, inputs = node['class_type'], node.get('inputs', {})
        if kind in ('OneAPIQwenSpeech', 'OneAPIQwenTranscribe'):
            if inputs.get('device', 'cuda') == 'cpu':
                continue
            task = 'tts' if kind == 'OneAPIQwenSpeech' else 'asr'
            model = inputs.get('model', 'qwen3-tts-1.7b' if task == 'tts' else 'qwen3-asr-0.6b')
            working = (TTS_WORKING if task == 'tts' else ASR_WORKING).get(model, 6 * GIB)
            if inputs.get('word_timestamps'):
                working += 2.5 * GIB
            previous = speech.setdefault(task, {'models': set(), 'working_bytes': 0})
            previous['models'].add(model)
            previous['working_bytes'] = max(previous['working_bytes'], working)
            kinds.add(task)
        for key, group in LOADERS.get(kind, []):
            name = inputs.get(key)
            if isinstance(name, str):
                assets.add((group, name))
            else:
                unknown = True
        for key in ('width', 'height', 'length', 'batch_size'):
            if not isinstance(inputs.get(key), (int, float)):
                continue
            if key == 'width' and isinstance(inputs.get('height'), (int, float)):
                pixels = max(pixels, inputs[key] * inputs['height'])
            elif key == 'length':
                frames = max(frames, inputs[key])
            elif key == 'batch_size':
                batch = max(batch, inputs[key])
    if assets or unknown:
        video = frames > 1 or any('minimax_h3' in name or 'sol-refiner' in name or 'ltx-' in name for _, name in assets)
        kinds.add('video' if video else 'image')
    else:
        video = False
    return {'assets': sorted(assets), 'speech': speech, 'kinds': sorted(kinds), 'unknown': unknown,
            'pixels': pixels, 'frames': frames, 'batch': batch, 'video': video}


class GPUPool:
    def __init__(self):
        self.idle_timeout_seconds = 120
        self.resources = {}
        self.last_used = {}
        self.native_groups = OrderedDict()
        self.before_native = set()
        self.decisions = OrderedDict()
        self.events = []
        self.lock = threading.Lock()
        self.last_activity = time.monotonic()
        self.installed = False

    def register(self, name, state, release):
        self.resources[name] = (state, release)
        self.last_used[name] = 0

    def states(self):
        return {name: state() for name, (state, _) in self.resources.items()}

    def native_models(self):
        device = memory.get_torch_device()
        if device.type == 'cpu':
            return []
        return [model for model in memory.loaded_models() if model is not None and model.load_device == device]

    def native_bytes(self):
        return sum(model.loaded_size() for model in self.native_models())

    def native_members(self, assets):
        group = self.native_groups.get(tuple(assets))
        if group is None:
            return []
        loaded = {id(model) for model in self.native_models()}
        return [model for ref in group['refs'] if (model := ref()) is not None and id(model) in loaded]

    def working_bytes(self, requirements, total, reserve):
        weights = 0
        unknown = requirements['unknown']
        for group, name in requirements['assets']:
            path = folder_paths.get_full_path(group, name)
            if path:
                weights += Path(path).stat().st_size
            else:
                unknown = True
        # Native models can offload their weights; leave room for the CUDA context.
        limit = max(0, total - reserve - 0.75 * GIB)
        if requirements['assets'] or unknown:
            scale = max(1, requirements['pixels'] / (512 * 512)) * max(1, requirements['batch'])
            workspace = (4 if requirements['video'] else 1.5) * GIB * scale
            native = min(limit, weights * 1.1 + workspace) if not unknown else limit
        else:
            native = 0
        return native, sum(item['working_bytes'] for item in requirements['speech'].values())

    def prepare(self, graph):
        started = time.perf_counter()
        needs = graph_requirements(graph)
        device = memory.get_torch_device()
        decision = {'kinds': needs['kinds'], 'models': [name for _, name in needs['assets']], 'evictions': []}
        if device.type == 'cpu' or not needs['kinds']:
            return {**decision, 'action': 'no_gpu_required', 'scheduling_seconds': time.perf_counter() - started}
        total = memory.get_total_memory(device)
        reserve = memory.extra_reserved_memory()
        native, speech = self.working_bytes(needs, total, reserve)
        states = self.states()
        reused = 0
        protected = set()
        for name, request in needs['speech'].items():
            state = states.get(name)
            if state and state['device'] == 'cuda' and request['models'].issubset(state['models']):
                reused += min(state['vram_bytes'], request['working_bytes'])
                protected.add(name)
        keep_native = self.native_members(needs['assets'])
        reused += min(native, sum(model.loaded_size() for model in keep_native))
        required = min(total - 0.75 * GIB, max(0, native + speech - reused) + reserve)
        free = memory.get_free_memory(device)
        decision.update(free_before_bytes=free, estimated_working_bytes=native + speech, reusable_bytes=reused,
                        headroom_bytes=reserve, required_free_bytes=required)
        candidates = [(self.last_used[name], name) for name, state in states.items()
                      if name not in protected and state and state['device'] == 'cuda']
        unused_native = {id(model) for model in self.native_models() if model not in keep_native}
        if unused_native:
            ages = [group['last_used'] for group in self.native_groups.values() if any(id(ref()) in unused_native for ref in group['refs'])]
            candidates.append((min(ages, default=0), 'native'))
        for _, name in sorted(candidates):
            if free >= required:
                break
            before = free
            if name == 'native':
                memory.free_memory(required, device, keep_loaded=[memory.LoadedModel(model) for model in keep_native])
            else:
                self.resources[name][1]()
            free = memory.get_free_memory(device)
            decision['evictions'].append({'resource': name, 'reason': 'memory_pressure', 'freed_bytes': max(0, free - before)})
        decision.update(free_after_bytes=free, budget_satisfied=free >= required,
                        action='evict_pressure' if decision['evictions'] else 'reuse_or_keep',
                        scheduling_seconds=time.perf_counter() - started)
        return decision

    def prepare_speech(self, task, model, device, word_timestamps=False):
        if device == 'cpu':
            return
        kind = 'OneAPIQwenSpeech' if task == 'tts' else 'OneAPIQwenTranscribe'
        self.prepare({'speech': {'class_type': kind, 'inputs': {'model': model, 'device': device, 'word_timestamps': word_timestamps}}})

    def collect_idle(self):
        if time.monotonic() - self.last_activity < self.idle_timeout_seconds:
            return
        released = []
        for name, state in self.states().items():
            if state and state['device'] == 'cuda':
                self.resources[name][1]()
                released.append(name)
        device = memory.get_torch_device()
        if device.type != 'cpu' and self.native_bytes():
            memory.free_memory(1e30, device)
            released.append('native')
        self.last_activity = time.monotonic()
        if released:
            with self.lock:
                self.events.append({'reason': 'idle_timeout', 'resources': released, 'timestamp': time.time()})
                self.events = self.events[-32:]

    def install(self, queue):
        original_get, original_done = queue.get, queue.task_done

        def get(timeout=None):
            remaining = max(0.1, self.idle_timeout_seconds - (time.monotonic() - self.last_activity))
            entry = original_get(timeout=min(timeout, remaining) if timeout is not None else remaining)
            if entry is None:
                try:
                    self.collect_idle()
                except Exception:
                    self.last_activity = time.monotonic()
                    logging.exception('OneAPI GPU pool idle cleanup failed')
                return None
            item, _ = entry
            self.before_native = {id(model) for model in self.native_models()}
            try:
                decision = self.prepare(item[2])
            except Exception:
                # Pool bookkeeping must not terminate ComfyUI's execution thread.
                logging.exception('OneAPI GPU pool preparation failed; continuing with native model management')
                decision = {'action': 'native_fallback', 'evictions': []}
            decision.update(job_id=item[1], owner=item[3].get('user_id'), timestamp=time.time())
            with self.lock:
                self.decisions[item[1]] = decision
                while len(self.decisions) > 128:
                    self.decisions.popitem(last=False)
            return entry

        def done(item_id, history_result, **kwargs):
            with queue.mutex:
                item = queue.currently_running[item_id]
            try:
                needs = graph_requirements(item[2])
                self.last_activity = time.monotonic()
                for name in needs['speech']:
                    self.last_used[name] = self.last_activity
                if needs['assets']:
                    key = tuple(needs['assets'])
                    refs = self.native_groups.get(key, {}).get('refs', [])
                    members = {id(model): model for ref in refs if (model := ref()) is not None}
                    members.update({id(model): model for model in self.native_models() if id(model) not in self.before_native})
                    self.native_groups[key] = {'refs': [weakref.ref(model) for model in members.values()], 'last_used': self.last_activity}
                    self.native_groups.move_to_end(key)
                    while len(self.native_groups) > 16:
                        self.native_groups.popitem(last=False)
                with self.lock:
                    if item[1] in self.decisions:
                        self.decisions[item[1]]['resources_after'] = self.states()
                        self.decisions[item[1]]['native_cached_after_bytes'] = self.native_bytes()
            except Exception:
                logging.exception('OneAPI GPU pool bookkeeping failed; preserving native completion')
            return original_done(item_id, history_result, **kwargs)

        queue.get, queue.task_done = get, done
        self.installed = True
        # The worker may already be waiting in the previous get() at HTTP startup.
        with queue.not_empty:
            queue.not_empty.notify_all()

    def decision(self, job_id):
        with self.lock:
            item = self.decisions.get(job_id)
            return {k: v for k, v in item.items() if k != 'owner'} if item else None

    def status(self, owner):
        device = memory.get_torch_device()
        with self.lock:
            recent = [{k: v for k, v in item.items() if k != 'owner'} for item in self.decisions.values() if item['owner'] == owner]
            events = list(self.events)
        return {'enabled': self.installed, 'policy': 'reuse_when_capacity_allows_lru_under_pressure',
                'idle_timeout_seconds': self.idle_timeout_seconds, 'device': str(device),
                'total_bytes': memory.get_total_memory(device), 'free_bytes': memory.get_free_memory(device),
                'headroom_bytes': memory.extra_reserved_memory(), 'resources': self.states(),
                'native_cached_bytes': self.native_bytes(),
                'native_groups': [{'models': [name for _, name in key], 'vram_bytes': sum(model.loaded_size() for model in self.native_members(key))} for key in list(self.native_groups)], 'recent': recent, 'idle_events': events}


GPU_POOL = GPUPool()
