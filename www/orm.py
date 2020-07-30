import asyncio, logging, aiomysql  # 为数据库提供异步IO大驱动


def log(sql, args=()):
    logging.info('SQL: %s' % sql)


# 创建连接池
# 我们需要创建一个全局的连接池，每个HTTP请求都可以从连接池中直接获取数据库连接。
# 使用连接池的好处是不必频繁地打开和关闭数据库连接，而是能复用就尽量复用。
# loop是消息循环，不断接收消息/处理消息
async def create_pool(loop, **kw):
    logging.info('create database connection pool...')
    global __pool
    __pool = await aiomysql.create_pool(
        host=kw.get('host', 'localhost'),
        port=kw.get('port', 3306),
        user=kw['user'],
        password=kw['password'],
        db=kw['db'],
        charset=kw.get('charset', 'utf-8'),
        autocommit=kw.get('autocommit', True),
        maxsize=kw.get('maxsize', 10),
        minsize=kw.get('minsize', 1),
        loop=loop
    )


# 执行select语句
# SQL语句的占位符是?，而MySQL的占位符是%s，select()函数在内部自动替换。
# 注意要始终坚持使用带参数的SQL，而不是自己拼接SQL字符串，这样可以防止SQL注入攻击。
async def select(sql, args, size=None):
    log(sql, args)
    global __pool
    with (await __pool) as conn:
        try:
            cur = await conn.cursor(aiomysql.DictCursor)
            await cur.execute(sql.replace('?', '%s'), args or ())
            affected = cur.rowcount
            await cur.close()
        except BaseException as e:
            raise
        return affected


# 执行insert update delete语句，定义个通用的execute（）函数
# 这三种sql的执行都需要相同的参数args，以及返回一个整数表示影响的行数affected
async def execute(sql, args):
    log(sql)
    with (await __pool) as conn:
        try:
            cur = await conn.cursor()
            await cur.execute(sql.replace('?', '%s'), args)
            affected = cur.rowcount
            await cur.close()
        except BaseException as e:
            raise
        return affected

#filed字段类型
class Field(object):

    def __init__(self, name, column_type, primary_key, default):
        self.name = name
        self.column_type = column_type
        self.primary_key = primary_key
        self.default = default

    def __str__(self):
        return '<%s, %s:%s>' % (self.__class__.__name__, self.column_type, self.name)

class StringField(Field):

    def __init__(self, name=None, primary_key=False, default=None, ddl='varchar(100)'):
        super().__init__(name, ddl, primary_key, default)

class BooleanField(Field):

    def __init__(self, name=None, default=False):
        super().__init__(name, 'boolean', False, default)

class IntegerField(Field):

    def __init__(self, name=None, primary_key=False, default=0):
        super().__init__(name, 'bigint', primary_key, default)

class FloatField(Field):

    def __init__(self, name=None, primary_key=False, default=0.0):
        super().__init__(name, 'real', primary_key, default)

class TextField(Field):

    def __init__(self, name=None, default=None):
        super().__init__(name, 'text', False, default)


#Model只是一个基类，要将具体的子类如User的映射信息读取出来需要通过metaclass
#这样，任何继承自model的类（如user），会自动通过modelmetaclass扫描映射关系，
#并存储到自身的类属性如__table__/__mappings__中
def create_args_strings(num):
    L = []
    for n in range(num):
        L.append('?')
    return ', '.join(L)

class ModelMetaclass(type):

    def __new__(cls, name, bases, attrs):
        #判断name是否为Model，如果是则不处理，反之则进行改写
        if name == 'Model':
            return type.__new__(cls, name, bases, attrs)
        #获取table名称：
        tableName = attrs.get('__table__', None)
        logging.info('found model: %s (table: %s)') % (name, tableName)
        #获取所有的Field和主键名
        mappings = dict()    #定义mapping搜集类attrs中的field变量，并且将这些变量从attrs踢出去
        fields = []
        primaryKey = None
        for k, v in attrs.items():
            if isinstance(v, Field):
                logging.info('  found mapping: %s ==> %s' % (k, v))
                mappings[k] = v
                if v.primary_key:
                    #找到主键
                    if primaryKey:
                        raise RuntimeError('Duplicate primary key for field: %s' % k)
                else:
                    fields.append(k)
        if not primaryKey:
            raise RuntimeError('Primary key not found.')
        for k in mappings.keys():
            attrs.pop(k)
        escaped_fields = list(map(lambda f:'%s' % f, fields))  #除主键外的其他键名
        attrs['__mappings__'] = mappings #保存属性和列的映射关系
        attrs['__table__'] = tableName
        attrs['__primary_key__'] = primaryKey   #主键属性名
        attrs['__field__'] = fields          #除主键外的属性名

        # 构造默认的select，insert， update和delete语句：
        attrs['__select__'] = 'select `%s`, %s from `%s`' % (primaryKey, ', '.join(escaped_fields), tableName)
        attrs['__insert__'] = 'insert `%s` (%s, `%s`) values (%s)' % (tableName, ', '.join(escaped_fields), primaryKey, create_args_strings(len(escaped_fields)+1))
        attrs['__update__'] = 'update `%s` (%s, `%s`) values (%s)' % (tableName, ', '.join(map(lambda f: '`%s`=?' % (mappings.get(f).name or f), fields)),primaryKey)
        attrs['__delete__'] = 'delete from `%s` where `%s`=?' % (tableName, primaryKey)
        return type.__new__(cls, name, bases, attrs)


#定义所有orm映射的基类Model
#python中，如果类中乳腺了自定义metaclass，编译器会默认调用对应class下的__new__函数
class Model(dict, metaclass=ModelMetaclass):

    def __init__(self, **kw):
        super(Model, self).__init__(**kw)

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(r"'Model' object has no attribute '%s'" % key)

    def __setattr__(self, key, value):
        self[key] = value

    def getValue(self, key):
        return getattr(self, key, None)

    def getValueOrDefault(self, key):
        value = getattr(self, key, None)
        if value is None:
            field = self.__mappings__[key]
            if field.default is not None:
                value = field.default() if callable(field.default) else field.default
                logging.debug('using default value for %s: %s ' % (key, str(value)))
                setattr(self, key, value)
        return value

    #往Model类中添加类方法，可以让所有子类调用
    @classmethod
    async def findAll(cls, where=None, args=None, **kw):
        ## find objects by where clause
        sql = [cls.__select__ ]
        if where:
            sql.append('where')
            sql.append(where)
        if args is None:
            args = []
        orderBy = kw.get('orderBy', None)
        if orderBy:
            sql.append('order by')
            sql.append(orderBy)
        limit = kw.get('limit', None)
        if limit is not None:
            sql.append('limit')
            if isinstance(limit, int):
                sql.append('?')
                sql.append(limit)
            elif isinstance(limit, tuple) and len(limit) == 2:
                sql.append('?, ?')
                args.append(limit)
            else:
                raise ValueError('Invalid limit value: %s' % str(limit))
        rs = await select(' '.join(sql), args)   # 查询select所有objects，异步操作
        return [cls(**r) for r in rs]

    @classmethod
    async def find(cls, pk):
        ## find object by primary key
        rs = await select('%s where `%s`=?' % (cls.__select__, cls.__primary_key__), [pk], 1)
        if len(rs) == 0:
            return None
        return cls(**rs[0])

    ## 添加实例方法
    async def save(self):
        args = list(map(self.getValueOrDefault, self.__fields__))
        args.append(self.getValueOrDefault(self.__primary_key__))
        rows = await execute(self.__insert__, args)
        if rows != 1:
            logging.warning('failed to insert record: affected rows: %s' % rows)

    async def update(self):
        args = list(map(self.getValue, self.__fields__))
        args.append(self.getValue(self.__primary_key__))
        rows = await execute(self.__update__, args)
        if rows != 1:
            logging.warning('failed to update by primary key: affected rows:%s' % rows)

    async def remove(self):
        args = [self.getValue(self.__primary_key__)]
        rows = await execute(self.__delete__, args)
        if rows != 1:
            logging.warning('failed to remove by primary key : affected rows: %s '% rows)







