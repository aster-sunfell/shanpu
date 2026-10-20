INSERT INTO t_product (id, title, price, stock, status)
VALUES (5, '并发测试商品', 300, 10, 1)
ON DUPLICATE KEY UPDATE stock = 10;
