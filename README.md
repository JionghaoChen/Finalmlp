# FinalMLP By ReChorus

## 数据集选择

由于原始数据集过大，本项目选择使用 **mind-small** 数据集。

## 数据下载与预处理

由于GitHub文件大小限制，未将数据集上传，需要另行下载

## 问题与解决

### 数据类型不一致导致 `item_meta` 文件为空

**问题描述**： 在运行 `MovieLens-1m.ipynb` 时，由于 `item_meta.movieId` 和 `interaction_ctr.news_id` 的数据类型不一致，导致生成的 `item_meta` 文件为空。

**解决方案**： 在代码中预处理这两个字段，统一将它们的数据类型转换为 `int` 类型。

## 联系我们

郭泽涛 陈炯浩

------

感谢您对本项目的关注与支持！
