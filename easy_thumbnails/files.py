from PIL import Image
from django.core.files.base import File, ContentFile
from django.core.files.storage import get_storage_class, default_storage
from django.db.models.fields.files import ImageFieldFile, FieldFile
from django.utils.html import escape
from django.utils.safestring import mark_safe
from easy_thumbnails import engine, utils
import os


DEFAULT_THUMBNAIL_STORAGE = get_storage_class(
                                        utils.get_setting('DEFAULT_STORAGE'))()


def get_thumbnailer(source, relative_name=None):
    """
    Get a thumbnailer for a source file.
    
    """
    if isinstance(source, Thumbnailer):
        return source
    elif isinstance(source, FieldFile):
        if not relative_name:
            relative_name = source.name
        return ThumbnailerFieldFile(source.instance, source.field,
                                    relative_name)
    elif isinstance(source, File):
        return Thumbnailer(source.file, relative_name)
    raise TypeError('The source object must either be a Thumbnailer, a '
                    'FieldFile or a File with the relative_name argument '
                    'provided.')


def save_thumbnail(thumbnail_file, storage):
    """
    Save a thumbnailed file.
    
    """
    filename = thumbnail_file.name
    if storage.exists(filename):
        try:
            storage.delete(filename)
        except:
            pass
    return storage.save(filename, thumbnail_file)


class FakeField(object):
    name = 'fake'

    def __init__(self, storage=None):
        self.storage = storage or default_storage

    def generate_filename(self, instance, name, *args, **kwargs):
        return name


class FakeInstance(object):
    def save(self, *args, **kwargs):
        pass


class ThumbnailFile(ImageFieldFile):
    """
    A thumbnailed file.
    
    """
    def __init__(self, name, file=None, storage=None, *args, **kwargs):
        fake_field = FakeField(storage=storage)
        super(ThumbnailFile, self).__init__(FakeInstance(), fake_field, name,
                                            *args, **kwargs)
        if file:
            self.file = file

    def _get_image(self):
        """
        Get a PIL image instance of this file.
        
        The image is cached to avoid the file needing to be read again if the
        function is called again.
        
        """
        if not hasattr(self, '_image_cache'):
            self.image = Image.open(self)
        return self._image_cache

    def _set_image(self, image):
        """
        Set the image for this file.
        
        This also caches the dimensions of the image. 
        
        """
        if image:
            self._image_cache = image
            self._dimensions_cache = image.size
        else:
            if hasattr(self, '_image_cache'):
                del self._cached_image
            if hasattr(self, '_dimensions_cache'):
                del self._dimensions_cache

    image = property(_get_image, _set_image)

    def tag(self, alt='', use_size=True, **attrs):
        """
        Return a standard XHTML ``<img ... />`` tag for this field.
        
        """
        attrs['alt'] = escape(alt)
        attrs['src'] = escape(self.url)
        if use_size:
            attrs.update(dict(width=self.width, height=self.height))
        attrs = ' '.join(['%s="%s"' % (key, escape(value))
                          for key, value in attrs.items()])
        return mark_safe('<img %s />' % attrs)

    tag = property(tag)

    def _get_file(self):
        self._require_file()
        if not hasattr(self, '_file') or self._file is None:
            self._file = self.storage.open(self.name, 'rb')
        return self._file

    def _set_file(self, file):
        self._file = file

    def _del_file(self):
        del self._file

    file = property(_get_file, _set_file, _del_file)


class Thumbnailer(File):
    """
    A file-like object which provides some methods to generate thumbnail
    images.

    """
    thumbnail_basedir = utils.get_setting('BASEDIR')
    thumbnail_subdir = utils.get_setting('SUBDIR')
    thumbnail_prefix = utils.get_setting('PREFIX')
    thumbnail_quality = utils.get_setting('QUALITY')
    thumbnail_extension = utils.get_setting('EXTENSION')

    def __init__(self, file, name=None, source_storage=None,
                 thumbnail_storage=None, *args, **kwargs):
        super(Thumbnailer, self).__init__(file, name, *args, **kwargs)
        self.source_storage = source_storage or default_storage
        self.thumbnail_storage = (thumbnail_storage or
                                  DEFAULT_THUMBNAIL_STORAGE)

    def generate_thumbnail(self, thumbnail_options):
        """
        Return a ``ThumbnailFile`` containing a thumbnail image.
        
        The thumbnail image is generated using the ``thumbnail_options``
        dictionary.
        
        """
        thumbnail_image = engine.process_image(self.image, thumbnail_options)
        quality = thumbnail_options.get('quality', self.thumbnail_quality)
        data = engine.save_image(thumbnail_image, quality=quality).read()

        filename = self.get_thumbnail_name(thumbnail_options)
        thumbnail = ThumbnailFile(filename, ContentFile(data))
        thumbnail.image = thumbnail_image
        thumbnail._committed = False

        return thumbnail

    def get_thumbnail_name(self, thumbnail_options):
        """
        Return a thumbnail filename for the given ``thumbnail_options``
        dictionary and ``source_name`` (which defaults to the File's ``name``
        if not provided).
        
        """
        path, source_filename = os.path.split(self.name)
        source_extension = os.path.splitext(source_filename)[1][1:]
        filename = '%s%s' % (self.thumbnail_prefix, source_filename)
        extension = (self.thumbnail_extension or source_extension.lower()
                     or 'jpg')

        thumbnail_options = thumbnail_options.copy()
        size = tuple(thumbnail_options.pop('size'))
        quality = thumbnail_options.pop('quality', self.thumbnail_quality)
        initial_opts = ['%sx%s' % size, 'q%s' % quality]

        opts = thumbnail_options.items()
        opts.sort()   # Sort the options so the file name is consistent.
        opts = ['%s' % (v is not True and '%s-%s' % (k, v) or k)
                for k, v in opts if v]

        all_opts = '_'.join(initial_opts + opts)

        data = {'opts': all_opts}
        basedir = self.thumbnail_basedir % data
        subdir = self.thumbnail_subdir % data

        filename_parts = [filename]
        if ('%(opts)s' in self.thumbnail_basedir or
            '%(opts)s' in self.thumbnail_subdir):
            if extension != source_extension:
                filename_parts.append(extension)
        else:
            filename_parts += [all_opts, extension]
        filename = '.'.join(filename_parts)

        return os.path.join(basedir, path, subdir, filename)

    def get_thumbnail(self, thumbnail_options, save=True):
        """
        Return a ``ThumbnailFile`` containing a thumbnail.
        
        It the file already exists, it will simply be returned.
        
        Otherwise a new thumbnail image is generated using the
        ``thumbnail_options`` dictionary. If the ``save`` argument is ``True``
        (default), the generated thumbnail will be saved too.
                        
        """
        name = self.get_thumbnail_name(thumbnail_options)

        if self.thumbnail_exists(thumbnail_options):
            thumbnail = ThumbnailFile(name=name,
                                      storage=self.thumbnail_storage)
            return thumbnail

        thumbnail = self.generate_thumbnail(thumbnail_options)

        if save and self.name:
            save_thumbnail(thumbnail, self.thumbnail_storage)
            if self.source_storage != self.thumbnail_storage:
                # If the source storage is local and the thumbnail storage is
                # remote, save a copy of the thumbnail there too. This helps to
                # keep the testing of thumbnail existence as a local activity.
                try:
                    self.thumbnail_storage.path(name)
                except NotImplementedError:
                    try:
                        self.field_storage.path(name)
                    except NotImplementedError:
                        pass
                    else:
                        self.save_thumbnail(thumbnail, self.field_storage)
        return thumbnail

    def thumbnail_exists(self, thumbnail_options):
        """
        Calculate whether the thumbnail already exists and that the source is
        not newer than the thumbnail.
        
        If neither the source nor the thumbnail are using local storages, only
        the existance of the thumbnail will be checked.
        
        """
        filename = self.get_thumbnail_name(thumbnail_options)

        try:
            source_path = self.source_storage.path(self.name)
        except NotImplementedError:
            source_path = None
        try:
            thumbnail_path = self.thumbnail_storage.path(filename)
        except NotImplementedError:
            thumbnail_path = None

        if not source_path and not thumbnail_path:
            # This is the worst-case scenario - neither storage was local so
            # this will cause a remote existence check.
            return self.thumbnail_storage.exists(filename)

        # If either storage wasn't local, use the other for the path.
        if not source_path:
            source_path = self.thumbnail_storage.path(self.name)
        if not thumbnail_path:
            thumbnail_path = self.source_storage.path(filename)

        if os.path.isfile(thumbnail_path):
            if not os.path.isfile(source_path):
                return True
        else:
            return False
        return (os.path.getmtime(source_path) <=
                os.path.getmtime(thumbnail_path))

    def _image(self):
        if not hasattr(self, '_cached_image'):
            # TODO: Use different methods of generating the file, rather than
            # just relying on PIL.
            self._cached_image = Image.open(self)
            # Image.open() is a lazy operation, so force the load so we
            # can close this file again if appropriate.
            self._cached_image.load()
        return self._cached_image

    image = property(_image)


class ThumbnailerFieldFile(FieldFile, Thumbnailer):
    """
    A field file which provides some methods for generating (and returning)
    thumbnail images.
    
    """
    def __init__(self, *args, **kwargs):
        super(ThumbnailerFieldFile, self).__init__(*args, **kwargs)
        self.source_storage = self.field.storage
        thumbnail_storage = getattr(self.field, 'thumbnail_storage', None)
        if thumbnail_storage:
            self.thumbnail_storage = thumbnail_storage

    def save(self, name, content, *args, **kwargs):
        """
        Save the file.
        
        If the thumbnail storage is local and differs from the field storage,
        save a place-holder of the source file there too. This helps to keep
        the testing of thumbnail existence as a local activity.
        
        """
        super(ThumbnailerFieldFile, self).save(name, content, *args, **kwargs)
        # If the thumbnail storage differs and is local, save a place-holder of
        # the source file there too.
        if self.thumbnail_storage != self.field.storage:
            try:
                path = self.thumbnail_storage.path(self.name)
            except NotImplementedError:
                pass
            else:
                if not os.path.exists(path):
                    try:
                        os.makedirs(os.path.dirname(path))
                    except OSError:
                        pass
                    open(path, 'w').close()

# TODO: deletion should use the storage for listing and deleting.
#    def delete(self, *args, **kwargs):
#        """
#        Delete the image, along with any thumbnails which match the filename
#        pattern for this source image.
#        
#        """
#        super(ThumbnailFieldFile, self).delete(*args, **kwargs)


class ThumbnailerImageFieldFile(ImageFieldFile, ThumbnailerFieldFile):
    """
    A field file which provides some methods for generating (and returning)
    thumbnail images.
    
    """
    def save(self, name, content, *args, **kwargs):
        """
        Save the image.
        
        If the thumbnail storage is local and differs from the field storage,
        save a place-holder of the source image there too. This helps to keep
        the testing of thumbnail existence as a local activity.
        
        The image will be resized down using a ``ThumbnailField`` if
        ``resize_source`` (a dictionary of thumbnail options) is provided by
        the field.
        
        """
        options = getattr(self.field, 'resize_source', None)
        if options:
            if not 'quality' in options:
                options['quality'] = self.thumbnail_quality
            content = Thumbnailer(content).generate_thumbnail(options)
        super(ThumbnailerImageFieldFile, self).save(name, content, *args,
                                                    **kwargs)
