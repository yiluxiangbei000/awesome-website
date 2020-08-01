import asyncio, os, inspect, logging, functools
import types

from urllib import parse

from aiohttp import web

## 编写装饰函数
from apis import APIError


def get(path):
    # Define decorator @get('/path')
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kw):
            return func(*args, **kw)

        wrapper.__method__ = 'GET'
        wrapper.__route__ = path
        return wrapper

    return decorator


## 编写装饰函数@post()
def post(path):
    ##define the decorator @path
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kw):
            return func(*args, **kw)

        wrapper.__method__ = 'POST'
        wrapper.__route__ = path
        return wrapper

    return decorator


## 以下是RequestHandler需要定义的一些函数
def get_required_kw_args(fn):
    args = []
    params = inspect.signature(fn).parameters
    for name, param in params.items():
        if param.kind == inspect.Parameter.KEYWORD_ONLY and param.default == inspect.Parameter.empty:
            args.append(name)
    return tuple(args)


def get_named_kw_args(fn):
    # 获取命名关键字参数
    args = []
    params = inspect.signature(fn).parameters
    for name, param in params.items():
        if param.kind == inspect.Parameter.KEYWORD_ONLY:
            args.append(name)
    return tuple(args)


def has_named_kw_args(fn):
    # 判断是否存在命名关键字参数
    params = inspect.signature(fn).parameters
    for name, param in params.items():
        if param.kind == inspect.Parameter.KEYWORD_ONLY:
            return True


def has_var_kw_arg(fn):
    # 是否存在可变关键字参数
    params = inspect.signature(fn).parameters
    for name, param in params.items():
        if param.kind == inspect.Parameter.VAR_KEYWORD:
            return True


def has_request_arg(fn):
    sig = inspect.signature(fn)
    params = sig.parameters
    found = False
    for name, param in params.items():
        if name == 'request':
            found = True
            continue
        if found and (params.kind != inspect.Parameter.VAR_POSITIONAL
                      and param.kind != inspect.Parameter.KEYWORD_ONLY
                      and param.kind != inspect.Parameter.VAR_KEYWORD):
            raise ValueError(
                'request parameter must be the last named parameter in function: %s%s' % (fn.__name__, str(sig)))

        return found


## 定义RequestHandler从URL函数中分析其所需要接受的参数
class RequestHandler(object):
    # 处理请求
    def __init__(self, app, fn):
        self.app = app
        self._func = fn
        self._has_request_arg = has_request_arg(fn)
        self._has_var_kw_args = has_var_kw_arg(fn)
        self._named_kw_args = get_named_kw_args(fn)
        self._has_named_kw_args = has_named_kw_args(fn)
        self._required_kw_args = get_required_kw_args(fn)

    async def __call__(self, request):  # 调用
        kw = None
        if self._has_var_kw_args or self._has_named_kw_args or self._required_kw_args:  # 如果request有参数
            if request.method == 'POST':  # 如果请求方法为POST
                if not request.content_type:  # 如果不存在content-type，报错
                    return web.HTTPBadRequest(text='Miss Content-Type.')
                ct = request.content_type.lower()  # content-type小写
                if ct.startswith('application/json'):  # 如果contnt-type以'application/json'开头
                    params = await request.json()  # 获取json类型参数
                    if not isinstance(params, dict):  # 如果参数的类型不是字典dict，报错
                        return web.HTTPBadRequest(text='JSON body must be object')
                    kw = params  # 将参数赋值给kw
                elif ct.startswith('application/x-www-form-urlencoded') or ct.startswith('multipart/form-data'):
                    # 如果是表单类型
                    params = await request.post()  # 参数异步得到 通过post方法
                    kw = dict(**params)  # 参数变成字典赋值给kw
                else:
                    # 报错content-type类型
                    return web.HTTPBadRequest(text='Unsupported Content-Type: %s' % request.content_type)

            if request.method == 'GET':  # 如果是GET方法
                qs = request.query_string  # 请求串，eg: ?age=18&name=bob
                if qs:
                    kw = dict()  # 将请求参数以字典形式存放到kw
                    for k, v in parse.parse_qs(qs, True).items():
                        kw[k] = v[0]

        if kw is None:  # ??? match_info是啥？ 哪来的request方法和属性??
            kw = dict(**request.match_info)
        else:
            if not self._has_var_kw_args and self._named_kw_args:  # 如果RequestHandler实例既无 可变关键字参数 也无 命名关键字参数
                # remove all unnamed kw，除去所有的未命名kw
                copy = dict()
                for name in self._named_kw_args:
                    if name in kw:
                        copy[name] = kw[name]
                kw = copy
            # check named arg:
            for k, v in request.match_info.items():
                if k in kw:
                    logging.warning('Duplicate arg name in named arg and kw args: %s ' % k)

                kw[k] = v
        if self._has_request_arg:
            kw['request'] = request
        # check required kw:
        if self._required_kw_args:
            for name in self._required_kw_args:
                if not name in kw:
                    return web.HTTPBadRequest(text='Miss argument: %s' % name)
        logging.info('call with args: %s' % str(kw))
        try:
            r = await self._func(**kw) # 将参数kw传给func函数，并返回
            return r
        except APIError as e:
            return dict(error=e.error, data=e.data, message=e.message)

# 定义add_static函数，来注册static文件下的文件
def add_static(app):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
    app.router.add_static('/static/', path)
    logging.info('add static %s => %s' % ('/static/', path))

# 定义add_route函数，来注册一个URL处理函数
def add_route(app, fn):
    method = getattr(fn, '__method__', None)   # 获取method
    path = getattr(fn, '__route__', None)       # 获取路径
    if path is None or method is None:
        raise ValueError('@get or @post not defined in %s.' % str(fn))
    if not asyncio.iscoroutinefunction(fn) and not inspect.isgeneratorfunction(fn):  # 如果fn视图函数不是协程
        # fn = asyncio.coroutine(fn)  #python3.8弃用？
        fn = types.coroutine(fn)
    logging.info('add route %s %s => %s' % (method, path, fn.__name__), ','.join(inspect.signature(fn).parameters.keys()))
    app.router.add_route(method, path, RequestHandler(app, fn))


# 定义add_routes函数，自动把handler模块的所有符合条件的URL函数注册了
# 类似于Django里的那个 urls，注册URL和路径？？
def add_routes(app, module_name):
    n = module_name.rfind('.')  #找到.的位置
    if n == (-1):
        mod = __import__(module_name, globals(), locals())
    else:
        name = module_name[n+1:]
        mod = getattr(__import__(module_name[:n], globals(), locals(), [name]), name)
    for attr in dir(mod):
        if attr.startswith('_'):
            continue
        fn = getattr(mod, attr)
        if callable(fn):
            method = getattr(fn, '__method__', None)
            path = getattr(fn, '__route__', None)
            if method and path:
                add_route(app, fn)

