SET NAMES utf8mb4;

-- 测试用户：username=summer, password=123456
INSERT INTO t_user (username, password, nickname, points)
VALUES ('summer', '$2a$10$fBeV768zLk6uXsgo3qJ9.epYnyamSrcbthxG32DeId6jxoMiIG8IO', 'summer', 10000)
ON DUPLICATE KEY UPDATE id = id;

INSERT INTO t_point_log (user_id, delta, balance_after, biz_type, biz_ref)
SELECT u.id, 10000, 10000, 1, ''
FROM t_user u
LEFT JOIN t_point_log p
  ON p.user_id = u.id
  AND p.biz_type = 1
  AND p.biz_ref = ''
WHERE u.username = 'summer'
  AND p.id IS NULL;

-- 仅补齐缺失数据，已有积分、库存、活动时间及订单保持原值。
-- 普通商品种子：5 件上架商品
INSERT INTO t_product (id, title, cover_url, description, price, stock, status) VALUES
  (1, '机械键盘 87 键',       '', '热插拔轴体，RGB 背光，PBT 键帽，适合办公与游戏。',     1200,  500, 1),
  (2, '无线鼠标',            '', '2.4G + 蓝牙双模，静音微动，800/1600/2400 三档 DPI。',   600, 1000, 1),
  (3, '降噪蓝牙耳机',        '', '主动降噪，单次续航 8 小时，附充电盒共 32 小时。',       2500,  200, 1),
  (4, '便携充电宝 20000mAh', '', '双向快充 22.5W，支持 Type-C / Lightning 输入输出。',     800,  300, 1),
  (5, '限量联名马克杯',      '', '陶瓷材质 380ml，限量发售，手慢无。',                     300,   10, 1)
ON DUPLICATE KEY UPDATE id = id;

-- 三档秒杀活动种子：100x 普惠、200x 限时、300x 超级
INSERT INTO t_seckill_activity (id, product_id, title, tier, seckill_price, total_stock, start_at, end_at, status) VALUES
  (1001, 1, '普惠专场 机械键盘',        1, 1080, 5000, NOW() - INTERVAL 1 HOUR, NOW() + INTERVAL 10 HOUR, 1),
  (1002, 2, '普惠专场 无线鼠标',        1,  540, 5000, NOW() - INTERVAL 1 HOUR, NOW() + INTERVAL 10 HOUR, 1),
  (1003, 3, '普惠专场 降噪蓝牙耳机',    1, 2250, 5000, NOW() - INTERVAL 1 HOUR, NOW() + INTERVAL 10 HOUR, 1),
  (2001, 4, '限时抢购 便携充电宝',      2,  560,  500, NOW() - INTERVAL 1 HOUR, NOW() + INTERVAL 10 HOUR, 1),
  (2002, 5, '限时抢购 联名马克杯',      2,  210,  500, NOW() - INTERVAL 1 HOUR, NOW() + INTERVAL 10 HOUR, 1),
  (2003, 1, '限时抢购 机械键盘',        2,  840,  500, NOW() + INTERVAL 20 MINUTE, NOW() + INTERVAL 10 HOUR, 0),
  (3001, 2, '超级秒杀 无线鼠标',        3,  180,  200, NOW() - INTERVAL 1 HOUR, NOW() + INTERVAL 10 HOUR, 1),
  (3002, 3, '超级秒杀 降噪蓝牙耳机',    3,  750,  200, NOW() + INTERVAL 30 MINUTE, NOW() + INTERVAL 8 HOUR, 0),
  (3003, 4, '超级秒杀 便携充电宝',      3,  240,  200, NOW() - INTERVAL 10 HOUR, NOW() - INTERVAL 8 HOUR, 2)
ON DUPLICATE KEY UPDATE id = id;
