# Fake-review classifier benchmark

Training data: `fake_reviews_dataset.csv`, 40401 reviews after removing exact duplicates.

| Model | In-domain acc | F1 | AUC | Cross-category mean | Worst category |
|---|---|---|---|---|---|
| LR (deployed) | 93.4% | 0.934 | 0.982 | 90.6% | 85.0% (Movies_and_TV_5) |
| LR + trigrams | 94.1% | 0.941 | 0.986 | 91.0% | 85.3% (Movies_and_TV_5) |
| LR, no stop words | 88.9% | 0.889 | 0.958 | 83.7% | 75.0% (Movies_and_TV_5) |
| Naive Bayes | 89.2% | 0.892 | 0.977 | 89.2% | 86.2% (Clothing_Shoes_and_Jewelry_5) |
| Linear SVM | 95.1% | 0.951 | 0.990 | 91.9% | 86.7% (Movies_and_TV_5) |
| Random Forest | 90.6% | 0.906 | 0.969 | 88.6% | 83.7% (Movies_and_TV_5) |

- **In-domain**: stratified 80/20 split of the training data.
- **Cross-category**: trained on 9 product categories, tested on the 10th, repeated for each category.
