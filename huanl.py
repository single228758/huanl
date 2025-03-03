import os
import json
import time
import struct
import random
import requests
import mimetypes
from pathlib import Path
from bridge.context import ContextType, Context
from bridge.reply import Reply, ReplyType
from channel.chat_message import ChatMessage
from common.log import logger
from plugins import Plugin, Event, EventAction, EventContext, register

@register(
    name="Huanl",
    desc="BeArt AI换脸插件",
    version="0.1",
    author="lanvent",
    desire_priority=-1
)
class HuanlPlugin(Plugin):
    # 默认请求头
    API_HEADERS = {
        "accept": "*/*",
        "accept-language": "zh-CN,zh;q=0.9",
        "origin": "https://beart.ai",
        "priority": "u=1, i",
        "product-code": "067003",
        "referer": "https://beart.ai/",
        "sec-ch-ua": '"Google Chrome";v="129", "Not=A?Brand";v="8", "Chromium";v="129"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
    }
    
    # 支持的图片格式
    SUPPORTED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif'}
    
    # MIME类型映射
    MIME_MAP = {
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.gif': 'image/gif',
        '.webp': 'image/webp',
        '.bmp': 'image/bmp'
    }

    def __init__(self):
        super().__init__()
        try:
            # 加载配置
            curdir = os.path.dirname(__file__)
            config_path = os.path.join(curdir, "config.json")
            if not os.path.exists(config_path):
                raise Exception("请先创建并配置config.json")
            
            with open(config_path, "r", encoding="utf-8") as f:
                self.config = json.load(f)
            
            # 初始化配置项
            self.trigger_prefix = self.config.get("trigger_prefix", "换脸")
            
            # 初始化状态管理
            self.waiting_for_images = {}  # 用户等待图片状态
            self.image_data = {}  # 存储用户上传的图片数据
            
            # 绑定事件处理
            self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
            
            logger.info("[Huanl] 插件初始化完成")
        except Exception as e:
            logger.error(f"[Huanl] 插件初始化失败: {e}")
            raise e

    def _validate_image(self, image_data):
        """验证图片格式"""
        try:
            header = image_data[:12]
            if any([
                header.startswith(b'\xFF\xD8\xFF'),  # JPEG
                header.startswith(b'\x89PNG\r\n\x1a\n'),  # PNG
                header.startswith(b'GIF87a') or header.startswith(b'GIF89a'),  # GIF
                header.startswith(b'RIFF') and header[8:12] == b'WEBP',  # WEBP
                header.startswith(b'BM')  # BMP
            ]):
                return True
            return False
        except:
            return False

    def _get_mime_type(self, image_data):
        """获取图片MIME类型"""
        header = image_data[:12]
        if header.startswith(b'\xFF\xD8\xFF'):
            return 'image/jpeg'
        elif header.startswith(b'\x89PNG\r\n\x1a\n'):
            return 'image/png'
        elif header.startswith(b'GIF87a') or header.startswith(b'GIF89a'):
            return 'image/gif'
        elif header.startswith(b'RIFF') and header[8:12] == b'WEBP':
            return 'image/webp'
        elif header.startswith(b'BM'):
            return 'image/bmp'
        return 'image/jpeg'  # 默认返回jpeg

    def on_handle_context(self, e_context: EventContext):
        """处理消息"""
        context = e_context["context"]
        
        # 获取用户信息
        if context.kwargs.get("isgroup", False):
            group_id = context["msg"].other_user_id
            user_id = context["msg"].actual_user_id
            session_id = f"{group_id}_{user_id}"
        else:
            session_id = context["msg"].from_user_id
            
        # 处理文本消息
        if context.type == ContextType.TEXT:
            content = context.content.strip()
            # 处理换脸触发命令
            if content == self.trigger_prefix:
                self.waiting_for_images[session_id] = "source"  # 等待源图片
                self.image_data[session_id] = {}
                reply = Reply(ReplyType.TEXT, "请发送一张带有人脸的源图片")
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return
                
        # 处理图片消息
        elif context.type == ContextType.IMAGE:
            if session_id not in self.waiting_for_images:
                return
                
            try:
                # 获取图片数据
                image_data = self._get_image_data(context)
                if not image_data:
                    reply = Reply(ReplyType.TEXT, "获取图片失败，请重试")
                    e_context["reply"] = reply
                    e_context.action = EventAction.BREAK_PASS
                    return
                
                # 验证图片格式
                if not self._validate_image(image_data):
                    reply = Reply(ReplyType.TEXT, "图片格式不支持，请使用jpg/png/gif/webp/bmp格式")
                    e_context["reply"] = reply
                    e_context.action = EventAction.BREAK_PASS
                    return
                    
                # 根据当前等待状态处理图片
                if self.waiting_for_images[session_id] == "source":
                    self.image_data[session_id]["source"] = image_data
                    self.waiting_for_images[session_id] = "target"  # 更新状态为等待目标图片
                    reply = Reply(ReplyType.TEXT, "请发送需要替换的目标人脸图片")
                    
                elif self.waiting_for_images[session_id] == "target":
                    self.image_data[session_id]["target"] = image_data
                    # 进行换脸处理
                    reply = self._process_face_swap(
                        self.image_data[session_id]["source"],
                        self.image_data[session_id]["target"]
                    )
                    # 清理状态
                    self.waiting_for_images.pop(session_id)
                    self.image_data.pop(session_id)
                    
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                
            except Exception as e:
                logger.error(f"[Huanl] 处理图片失败: {e}")
                self.waiting_for_images.pop(session_id, None)
                self.image_data.pop(session_id, None)
                reply = Reply(ReplyType.TEXT, f"处理失败: {str(e)}")
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS

    def _get_image_data(self, context):
        """获取图片数据"""
        try:
            msg = context.kwargs.get("msg")
            content = context.content
            
            # 如果已经是二进制数据，直接返回
            if isinstance(content, bytes):
                return content
                
            # 统一的文件读取函数
            def read_file(file_path):
                try:
                    with open(file_path, "rb") as f:
                        return f.read()
                except Exception as e:
                    logger.error(f"[Huanl] 读取文件失败 {file_path}: {e}")
                    return None
            
            # 按优先级尝试不同的读取方式
            if isinstance(content, str):
                # 1. 如果是文件路径，直接读取
                if os.path.isfile(content):
                    data = read_file(content)
                    if data:
                        return data
                
                # 2. 如果是URL，尝试下载
                if content.startswith(("http://", "https://")):
                    try:
                        response = requests.get(content, timeout=30)
                        if response.status_code == 200:
                            return response.content
                    except Exception as e:
                        logger.error(f"[Huanl] 从URL下载失败: {e}")
            
            # 3. 尝试从msg.content读取
            if hasattr(msg, "content") and os.path.isfile(msg.content):
                data = read_file(msg.content)
                if data:
                    return data
            
            # 4. 如果文件未下载，尝试下载
            if hasattr(msg, "_prepare_fn") and not msg._prepared:
                try:
                    msg._prepare_fn()
                    msg._prepared = True
                    time.sleep(1)  # 等待文件准备完成
                    
                    if hasattr(msg, "content") and os.path.isfile(msg.content):
                        data = read_file(msg.content)
                        if data:
                            return data
                except Exception as e:
                    logger.error(f"[Huanl] 下载图片失败: {e}")
            
            return None
            
        except Exception as e:
            logger.error(f"[Huanl] 获取图片数据失败: {e}")
            return None

    def _process_face_swap(self, source_image, target_image):
        """处理换脸请求"""
        try:
            # 创建换脸任务
            job_id = self._create_face_swap_job(source_image, target_image)
            if not job_id:
                return Reply(ReplyType.TEXT, "创建任务失败")
            
            # 获取结果
            result_url = self._get_face_swap_result(job_id)
            if not result_url:
                return Reply(ReplyType.TEXT, "处理失败")
            
            # 直接返回图片URL
            return Reply(ReplyType.IMAGE_URL, result_url)
            
        except Exception as e:
            logger.error(f"[Huanl] 换脸处理失败: {e}")
            return Reply(ReplyType.TEXT, f"处理失败: {str(e)}")

    def _create_face_swap_job(self, source_image, target_image):
        """创建换脸任务"""
        try:
            url = "https://api.beart.ai/api/beart/face-swap/create-job"
            headers = self.API_HEADERS.copy()
            headers.update({
                "product-serial": "7ccd9ec0944184501659484ed36d6550"
            })

            # 获取MIME类型
            source_mime = self._get_mime_type(source_image)
            target_mime = self._get_mime_type(target_image)

            # 生成随机文件名
            source_name = f"n_v{random.getrandbits(64):016x}.jpg"
            target_name = f"n_v{random.getrandbits(64):016x}.jpg"

            # 构建multipart/form-data请求
            files = {
                "target_image": (target_name, source_image, target_mime),
                "swap_image": (source_name, target_image, source_mime)
            }

            logger.info(f"[Huanl] 开始上传图片...")
            response = requests.post(url, headers=headers, files=files, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                if data.get("code") == 100000:
                    job_id = data["result"]["job_id"]
                    logger.info(f"[Huanl] 任务创建成功: {job_id}")
                    return job_id
                else:
                    logger.error(f"[Huanl] 服务器返回错误: {data.get('message', {}).get('zh', '未知错误')}")
            else:
                logger.error(f"[Huanl] 创建任务失败: HTTP {response.status_code}")
            return None
            
        except Exception as e:
            logger.error(f"[Huanl] 创建任务失败: {e}")
            return None

    def _get_face_swap_result(self, job_id, max_retries=30, interval=2):
        """获取换脸结果"""
        try:
            url = f"https://api.beart.ai/api/beart/face-swap/get-job/{job_id}"
            headers = self.API_HEADERS.copy()
            headers["content-type"] = "application/json; charset=UTF-8"
            
            logger.info(f"[Huanl] 等待处理结果，最多等待 {max_retries*interval} 秒...")
            for attempt in range(1, max_retries+1):
                try:
                    response = requests.get(url, headers=headers, timeout=15)
                    if response.status_code == 200:
                        data = response.json()
                        if data.get("code") == 100000:
                            logger.info("[Huanl] 处理完成")
                            return data["result"]["output"][0]  # 返回第一个结果URL
                        elif data.get("code") == 300001:  # 处理中
                            logger.info(f"[Huanl] 处理中... {attempt}/{max_retries}")
                            time.sleep(interval)
                            continue
                    
                    logger.error(f"[Huanl] 获取结果失败: {response.text}")
                    return None
                    
                except Exception as e:
                    logger.error(f"[Huanl] 获取结果出错: {e}")
                    time.sleep(interval)
            
            logger.error("[Huanl] 超过最大重试次数")
            return None
            
        except Exception as e:
            logger.error(f"[Huanl] 获取结果失败: {e}")
            return None 