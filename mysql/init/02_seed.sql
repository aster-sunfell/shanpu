SET NAMES utf8mb4;
USE shanpu;

-- 测试用户：username=summer, password=123456
INSERT IGNORE INTO t_user (username, password, nickname, points)
VALUES ('summer', '$2a$10$fBeV768zLk6uXsgo3qJ9.epYnyamSrcbthxG32DeId6jxoMiIG8IO', 'summer', 10000);

INSERT INTO t_point_log (user_id, delta, balance_after, biz_type, biz_ref)
SELECT u.id, 10000, 10000, 1, ''
FROM t_user u
LEFT JOIN t_point_log p
  ON p.user_id = u.id
  AND p.biz_type = 1
  AND p.biz_ref = ''
WHERE u.username = 'summer'
  AND p.id IS NULL;

-- 普通商品种子：5 件上架商品（id=5 库存 10，供并发防超卖验收）
INSERT INTO t_product (id, title, cover_url, description, price, stock, status) VALUES
  (1, '机械键盘 87 键',       '', '热插拔轴体，RGB 背光，PBT 键帽，适合办公与游戏。',     1200,  500, 1),
  (2, '无线鼠标',            '', '2.4G + 蓝牙双模，静音微动，800/1600/2400 三档 DPI。',   600, 1000, 1),
  (3, '降噪蓝牙耳机',        '', '主动降噪，单次续航 8 小时，附充电盒共 32 小时。',       2500,  200, 1),
  (4, '便携充电宝 20000mAh', '', '双向快充 22.5W，支持 Type-C / Lightning 输入输出。',     800,  300, 1),
  (5, '限量联名马克杯',      '', '陶瓷材质 380ml，限量发售，手慢无。',                     300,   10, 1);
