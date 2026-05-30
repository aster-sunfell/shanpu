export interface ProductVO {
  id: number;
  title: string;
  coverUrl: string;
  price: number;
  stock: number;
  status: number;
}

export interface ProductDetailVO extends ProductVO {
  description: string;
}
