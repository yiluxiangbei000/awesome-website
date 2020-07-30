import www.orm as orm
import asyncio
from www.models import User, Blog, Comment


async def test(loop):
    await orm.create_pool(loop=loop, user='root', password='913311670', db='awesome')
    u = User(name='Test', email='test@qq.com', passwd='12343243242', image='about:blank')
    await u.save()
    # 添加到数据库后需要关闭连接池，否则会报错：runtime error ： event loop is closed
    orm.__pool.close()
    await orm.__pool.wait_closed()

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(test(loop))
    loop.close()