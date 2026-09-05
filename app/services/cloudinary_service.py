"""
Servicio de Cloudinary para manejo de imágenes.
"""
import cloudinary
import cloudinary.uploader
from typing import Optional, List, Dict
import os

from app.config import settings


def configurar_cloudinary():
    """Configura Cloudinary con las credenciales del entorno."""
    cloudinary.config(
        cloud_name=settings.CLOUDINARY_CLOUD_NAME,
        api_key=settings.CLOUDINARY_API_KEY,
        api_secret=settings.CLOUDINARY_API_SECRET,
        secure=True
    )


class CloudinaryService:
    """Servicio para manejar operaciones con Cloudinary."""
    
    @staticmethod
    def upload_image(
        file,
        folder: str = "fashionstore",
        public_id: Optional[str] = None,
        transformation: Optional[Dict] = None
    ) -> Dict:
        """
        Sube una imagen a Cloudinary.
        
        Args:
            file: Archivo a subir (bytes o file-like)
            folder: Carpeta destino en Cloudinary
            public_id: ID público opcional para el archivo
            transformation: Transformaciones opcionales de imagen
        
        Returns:
            Diccionario con la respuesta de Cloudinary
        """
        configurar_cloudinary()
        
        options = {
            "folder": folder,
            "resource_type": "image",
        }
        
        if public_id:
            options["public_id"] = public_id
        
        if transformation:
            options["transformation"] = transformation
        
        result = cloudinary.uploader.upload(file, **options)
        
        return {
            "public_id": result.get("public_id"),
            "url": result.get("url"),
            "secure_url": result.get("secure_url"),
            "format": result.get("format"),
            "width": result.get("width"),
            "height": result.get("height"),
        }
    
    @staticmethod
    def upload_image_from_url(
        url: str,
        folder: str = "fashionstore",
        public_id: Optional[str] = None
    ) -> Dict:
        """Sube una imagen desde una URL."""
        configurar_cloudinary()
        
        options = {
            "folder": folder,
            "resource_type": "image",
        }
        
        if public_id:
            options["public_id"] = public_id
        
        result = cloudinary.uploader.upload(url, **options)
        
        return {
            "public_id": result.get("public_id"),
            "url": result.get("url"),
            "secure_url": result.get("secure_url"),
        }
    
    @staticmethod
    def delete_image(public_id: str) -> bool:
        """Elimina una imagen de Cloudinary."""
        configurar_cloudinary()
        
        result = cloudinary.uploader.destroy(public_id)
        return result.get("result") == "ok"
    
    @staticmethod
    def get_image_url(
        public_id: str,
        width: Optional[int] = None,
        height: Optional[int] = None,
        crop: str = "fill",
        quality: str = "auto",
        format: str = "auto"
    ) -> str:
        """Genera una URL transformada para una imagen."""
        configurar_cloudinary()
        
        transformations = []
        
        if width or height:
            transformations.append({
                "width": width,
                "height": height,
                "crop": crop,
            })
        
        transformations.append({
            "quality": quality,
            "fetch_format": format,
        })
        
        return cloudinary.url(public_id, transformation=transformations)
    
    @staticmethod
    def upload_audio(
        file,
        folder: str = "fashionstore/audio",
        public_id: Optional[str] = None
    ) -> Dict:
        """Sube un archivo de audio a Cloudinary."""
        configurar_cloudinary()
        
        options = {
            "folder": folder,
            "resource_type": "video",  # Cloudinary usa video para audio
        }
        
        if public_id:
            options["public_id"] = public_id
        
        result = cloudinary.uploader.upload(file, **options)
        
        return {
            "public_id": result.get("public_id"),
            "url": result.get("url"),
            "secure_url": result.get("secure_url"),
            "duration": result.get("duration"),
        }
    
    @staticmethod
    def generate_thumbnail(public_id: str, width: int = 200, height: int = 200) -> str:
        """Genera una miniatura de una imagen."""
        return CloudinaryService.get_image_url(
            public_id, width, height, crop="thumb"
        )
    
    @staticmethod
    def generate_product_image(public_id: str) -> Dict:
        """Genera múltiples tamaños para un producto."""
        return {
            "thumbnail": CloudinaryService.get_image_url(public_id, 100, 100),
            "small": CloudinaryService.get_image_url(public_id, 300, 300),
            "medium": CloudinaryService.get_image_url(public_id, 600, 600),
            "large": CloudinaryService.get_image_url(public_id, 1200, 1200),
            "original": CloudinaryService.get_image_url(public_id),
        }