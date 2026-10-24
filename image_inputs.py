import base64
import binascii
import io
import re
import uuid

import aiohttp
from aiohttp import web
from PIL import Image, UnidentifiedImageError

from comfy.cli_args import args
from video_workflows import upload_name


def normalize_image_content(settings):
    data = settings.copy()
    references, lines, aliases = [], [], {}

    def add_image(value, alias=None):
        if not isinstance(value, str) or not value:
            raise ValueError('Each reference needs an uploaded filename or data:image URL')
        index = len(references) + 1
        alias = f'image{index}' if alias is None else alias
        if not isinstance(alias, str) or not alias or alias in aliases:
            raise ValueError('Reference ids must be unique nonempty strings')
        item = {'index': index, 'id': alias, 'placeholder': f'<image{index}>', 'filename': value}
        references.append(item)
        aliases[alias] = item
        return f'[参考图{index} / {alias} = <image{index}>]'

    source = data.pop('input_image', None)
    if source:
        lines.append(add_image(source))
    images = data.pop('input_images', [])
    if not isinstance(images, list):
        raise ValueError('input_images must be a list')
    for image in images:
        lines.append(add_image(image.get('image'), image.get('id')) if isinstance(image, dict) else add_image(image))
    prompt = data.pop('prompt', '')
    if not isinstance(prompt, str):
        raise ValueError('prompt must be a string; use content for interleaved text and images')
    if prompt:
        lines.append(prompt)
    content = data.pop('content', None)
    messages = data.pop('messages', None)
    if messages is not None:
        if content is not None or not isinstance(messages, list):
            raise ValueError('Use content or messages, not both; messages must be a list')
        message = next((m for m in reversed(messages) if isinstance(m, dict) and m.get('role') == 'user'), None)
        if message is None:
            raise ValueError('messages needs a user message')
        content = message.get('content', '')
    if isinstance(content, str):
        lines.append(content)
    elif content is not None:
        if not isinstance(content, list):
            raise ValueError('content must be text or an ordered list of text/image parts')
        for part in content:
            if not isinstance(part, dict):
                raise ValueError('Each content part must be an object')
            kind = part.get('type')
            if kind in ('text', 'input_text'):
                if not isinstance(part.get('text'), str):
                    raise ValueError('Text content needs a text string')
                lines.append(part['text'])
            elif kind in ('image', 'image_url', 'input_image'):
                image = part.get('image', part.get('image_url'))
                if isinstance(image, dict):
                    image = image.get('url')
                lines.append(add_image(image, part.get('id')))
            elif kind == 'image_ref':
                if part.get('id') not in aliases:
                    raise ValueError('image_ref must name a previously supplied reference id')
                lines.append(aliases[part['id']]['placeholder'])
            else:
                raise ValueError('Unsupported content part: ' + str(kind))
    # Keep the existing single-file request's prompt unchanged.
    if content is None and len(references) <= 1 and not any(isinstance(x, dict) for x in images):
        text = prompt
    else:
        text = '\n'.join(lines)

    def replace_alias(match):
        if match[1] not in aliases:
            raise ValueError('Unknown reference id: ' + match[1])
        return aliases[match[1]]['placeholder']

    data['prompt'] = re.sub(r'<ref:([^<>]+)>', replace_alias, text)
    data['input_images'] = [item['filename'] for item in references]
    return data, references


async def prepare_image_settings(request, settings):
    if settings.get('task', 'image') != 'image' or 'workflow' in settings:
        return settings, []
    try:
        data, references = normalize_image_content(settings)
        for item in references:
            source = item['filename']
            if not source.startswith('data:'):
                if '://' in source:
                    raise ValueError('Upload remote images first, or pass data:image/...;base64,...')
                upload_name(source)
        if references:
            headers = {key: request.headers[key] for key in ('Authorization', 'Cookie', 'Comfy-User') if key in request.headers}
            async with aiohttp.ClientSession() as session:
                async with session.get(f'http://127.0.0.1:{args.port}/object_info/TextEncodeQwenImage21', headers=headers) as response:
                    response.raise_for_status()
                    info = await response.json()
                slots = info['TextEncodeQwenImage21']['input']['required']['images'][1]['template']['names']
                if len(references) > len(slots):
                    raise ValueError(f'Installed TextEncodeQwenImage21 exposes {len(slots)} reference inputs; received {len(references)}')
                for item in references:
                    source = item['filename']
                    if not source.startswith('data:'):
                        continue
                    header, separator, encoded = source.partition(',')
                    if not separator or not header.startswith('data:image/') or not header.endswith(';base64'):
                        raise ValueError('Inline images must use data:image/...;base64,...')
                    raw = base64.b64decode(encoded, validate=True)
                    with Image.open(io.BytesIO(raw)) as image:
                        image.verify()
                        extension = {value: key for key, value in Image.registered_extensions().items()}[image.format]
                        mime = Image.MIME.get(image.format, 'application/octet-stream')
                    form = aiohttp.FormData()
                    form.add_field('image', raw, filename=uuid.uuid4().hex + extension, content_type=mime)
                    async with session.post(f'http://127.0.0.1:{args.port}/upload/image', data=form, headers=headers) as response:
                        response.raise_for_status()
                        uploaded = await response.json()
                    item['filename'] = '/'.join(v for v in (uploaded.get('subfolder'), uploaded['name']) if v)
        data['input_images'] = [item['filename'] for item in references]
        return data, references
    except (ValueError, TypeError, KeyError, binascii.Error, UnidentifiedImageError, OSError) as error:
        raise web.HTTPBadRequest(text=str(error))
