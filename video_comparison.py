import json
from pathlib import Path
import uuid

from aiohttp import web
import folder_paths


WEB = Path(__file__).parent / 'video_web'
ASSETS = {'app.js', 'style.css'}


def catalog_for(request):
    owner = request.get('user_id')
    if not owner:
        raise web.HTTPUnauthorized()
    try:
        uuid.UUID(owner)
    except ValueError:
        raise web.HTTPForbidden()
    directory = Path(folder_paths.get_user_directory()) / 'video_comparison' / owner
    path = directory / 'catalog.json'
    if not path.is_file():
        raise web.HTTPNotFound(text='No video comparison catalog for this user')
    return directory, json.loads(path.read_text())


def register(routes, prefix):
    @routes.get(prefix + '/compare')
    async def page(request):
        catalog_for(request)
        return web.FileResponse(WEB / 'index.html')

    @routes.get(prefix + '/compare/assets/{name}')
    async def asset(request):
        name = request.match_info['name']
        if name not in ASSETS:
            raise web.HTTPNotFound()
        return web.FileResponse(WEB / name)

    @routes.get(prefix + '/comparison')
    async def catalog(request):
        _, data = catalog_for(request)
        records = []
        for item in data['records']:
            public = {key: value for key, value in item.items() if key not in ('media_path','poster','workflow')}
            public.update({kind + '_url': prefix + '/comparison/' + kind + '/' + item['id'] for kind in ('media','poster','workflow')})
            records.append(public)
        return web.json_response({**data, 'records': records}, headers={'Cache-Control':'no-store'})

    @routes.get(prefix + '/comparison/{kind}/{record_id}')
    async def evidence(request):
        directory, data = catalog_for(request)
        item = next((r for r in data['records'] if r['id'] == request.match_info['record_id']), None)
        kind = request.match_info['kind']
        if item is None or kind not in ('media','poster','workflow'):
            raise web.HTTPNotFound()
        base = Path(folder_paths.get_output_directory()) if kind == 'media' else directory
        path = (base / item['media_path' if kind == 'media' else kind]).resolve()
        if not path.is_relative_to(base.resolve()) or not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path)
