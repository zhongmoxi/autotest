"""
Library to perform pre/post test setup for KVM autotest.
"""
import os, sys, shutil, tempfile, re, ConfigParser, glob, inspect, commands
import logging
from autotest_lib.client.common_lib import error
from autotest_lib.client.bin import utils


@error.context_aware
def cleanup(dir):
    """
    If dir is a mountpoint, do what is possible to unmount it. Afterwards,
    try to remove it.

    @param dir: Directory to be cleaned up.
    """
    error.context("cleaning up unattended install directory %s" % dir)
    if os.path.ismount(dir):
        utils.run('fuser -k %s' % dir, ignore_status=True)
        utils.run('umount %s' % dir)
    if os.path.isdir(dir):
        shutil.rmtree(dir)


@error.context_aware
def clean_old_image(image):
    """
    Clean a leftover image file from previous processes. If it contains a
    mounted file system, do the proper cleanup procedures.

    @param image: Path to image to be cleaned up.
    """
    error.context("cleaning up old leftover image %s" % image)
    if os.path.exists(image):
        mtab = open('/etc/mtab', 'r')
        mtab_contents = mtab.read()
        mtab.close()
        if image in mtab_contents:
            utils.run('fuser -k %s' % image, ignore_status=True)
            utils.run('umount %s' % image)
        os.remove(image)


class Disk(object):
    """
    Abstract class for Disk objects, with the common methods implemented.
    """
    def __init__(self):
        self.path = None


    def setup_answer_file(self, filename, contents):
        utils.open_write_close(os.path.join(self.mount, filename), contents)


    def copy_to(self, src):
        dst = os.path.join(self.mount, os.path.basename(src))
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        elif os.path.isfile(src):
            shutil.copyfile(src, dst)


    def close(self):
        os.chmod(self.path, 0755)
        cleanup(self.mount)
        logging.debug("Disk %s successfuly set", self.path)


class FloppyDisk(Disk):
    """
    Represents a 1.44 MB floppy disk. We can copy files to it, and setup it in
    convenient ways.
    """
    @error.context_aware
    def __init__(self, path, qemu_img_binary, tmpdir):
        error.context("Creating unattended install floppy image %s" % path)
        self.tmpdir = tmpdir
        self.mount = tempfile.mkdtemp(prefix='floppy_', dir=self.tmpdir)
        self.virtio_mount = None
        self.path = path
        clean_old_image(path)
        if not os.path.isdir(os.path.dirname(path)):
            os.makedirs(os.path.dirname(path))

        try:
            c_cmd = '%s create -f raw %s 1440k' % (qemu_img_binary, path)
            utils.run(c_cmd)
            f_cmd = 'mkfs.msdos -s 1 %s' % path
            utils.run(f_cmd)
            m_cmd = 'mount -o loop,rw %s %s' % (path, self.mount)
            utils.run(m_cmd)
        except error.CmdError, e:
            cleanup(self.mount)
            raise


    def _copy_virtio_drivers(self, virtio_floppy):
        """
        Copy the virtio drivers on the virtio floppy to the install floppy.

        1) Mount the floppy containing the viostor drivers
        2) Copy its contents to the root of the install floppy
        """
        virtio_mount = tempfile.mkdtemp(prefix='virtio_floppy_',
                                        dir=self.tmpdir)

        pwd = os.getcwd()
        try:
            m_cmd = 'mount -o loop %s %s' % (virtio_floppy, virtio_mount)
            utils.run(m_cmd)
            os.chdir(virtio_mount)
            path_list = glob.glob('*')
            for path in path_list:
                self.copy_to(path)
        finally:
            os.chdir(pwd)
            cleanup(virtio_mount)


    def setup_virtio_win2003(self, virtio_floppy, virtio_oemsetup_id):
        """
        Setup the install floppy with the virtio storage drivers, win2003 style.

        Win2003 and WinXP depend on the file txtsetup.oem file to install
        the virtio drivers from the floppy, which is a .ini file.
        Process:

        1) Copy the virtio drivers on the virtio floppy to the install floppy
        2) Parse the ini file with config parser
        3) Modify the identifier of the default session that is going to be
           executed on the config parser object
        4) Re-write the config file to the disk
        """
        self._copy_virtio_drivers(virtio_floppy)
        txtsetup_oem = os.path.join(self.mount, 'txtsetup.oem')
        if not os.path.isfile(txtsetup_oem):
            raise IOError('File txtsetup.oem not found on the install '
                          'floppy. Please verify if your floppy virtio '
                          'driver image has this file')
        parser = ConfigParser.ConfigParser()
        parser.read(txtsetup_oem)
        if not parser.has_section('Defaults'):
            raise ValueError('File txtsetup.oem does not have the session '
                             '"Defaults". Please check txtsetup.oem')
        default_driver = parser.get('Defaults', 'SCSI')
        if default_driver != virtio_oemsetup_id:
            parser.set('Defaults', 'SCSI', virtio_oemsetup_id)
            fp = open(txtsetup_oem, 'w')
            parser.write(fp)
            fp.close()


    def setup_virtio_win2008(self, virtio_floppy):
        """
        Setup the install floppy with the virtio storage drivers, win2008 style.

        Win2008, Vista and 7 require people to point out the path to the drivers
        on the unattended file, so we just need to copy the drivers to the
        driver floppy disk.
        Process:

        1) Copy the virtio drivers on the virtio floppy to the install floppy
        """
        self._copy_virtio_drivers(virtio_floppy)


class CdromDisk(Disk):
    """
    Represents a CDROM disk that we can master according to our needs.
    """
    def __init__(self, path, tmpdir):
        self.mount = tempfile.mkdtemp(prefix='cdrom_unattended_', dir=tmpdir)
        self.path = path
        clean_old_image(path)
        if not os.path.isdir(os.path.dirname(path)):
            os.makedirs(os.path.dirname(path))


    @error.context_aware
    def close(self):
        error.context("Creating unattended install CD image %s" % self.path)
        g_cmd = ('mkisofs -o %s -max-iso9660-filenames '
                 '-relaxed-filenames -D --input-charset iso8859-1 '
                 '%s' % (self.path, self.mount))
        utils.run(g_cmd)

        os.chmod(self.path, 0755)
        cleanup(self.mount)
        logging.debug("unattended install CD image %s successfuly created",
                      self.path)


class UnattendedInstallConfig(object):
    """
    Creates a floppy disk image that will contain a config file for unattended
    OS install. The parameters to the script are retrieved from environment
    variables.
    """
    def __init__(self, test, params):
        """
        Sets class atributes from test parameters.

        @param test: KVM test object.
        @param params: Dictionary with test parameters.
        """
        root_dir = test.bindir
        images_dir = os.path.join(root_dir, 'images')
        self.deps_dir = os.path.join(root_dir, 'deps')
        self.unattended_dir = os.path.join(root_dir, 'unattended')

        attributes = ['kernel_args', 'finish_program', 'cdrom_cd1',
                      'unattended_file', 'medium', 'url', 'kernel', 'initrd',
                      'nfs_server', 'nfs_dir', 'install_virtio', 'floppy',
                      'cdrom_unattended', 'boot_path', 'extra_params',
                      'qemu_img_binary']

        for a in attributes:
            setattr(self, a, params.get(a, ''))

        if self.install_virtio == 'yes':
            v_attributes = ['virtio_floppy', 'virtio_storage_path',
                            'virtio_network_path', 'virtio_oemsetup_id',
                            'virtio_network_installer']
            for va in v_attributes:
                setattr(self, va, params.get(va, ''))

        self.tmpdir = test.tmpdir

        if getattr(self, 'unattended_file'):
            self.unattended_file = os.path.join(root_dir, self.unattended_file)

        if getattr(self, 'qemu_img_binary'):
            if not os.path.isfile(getattr(self, 'qemu_img_binary')):
                self.qemu_img_binary = os.path.join(root_dir,
                                                    self.qemu_img_binary)

        if getattr(self, 'cdrom_cd1'):
            self.cdrom_cd1 = os.path.join(root_dir, self.cdrom_cd1)
        self.cdrom_cd1_mount = tempfile.mkdtemp(prefix='cdrom_cd1_',
                                                dir=self.tmpdir)
        if self.medium == 'nfs':
            self.nfs_mount = tempfile.mkdtemp(prefix='nfs_',
                                              dir=self.tmpdir)

        if getattr(self, 'floppy'):
            self.floppy = os.path.join(root_dir, self.floppy)
            if not os.path.isdir(os.path.dirname(self.floppy)):
                os.makedirs(os.path.dirname(self.floppy))

        self.image_path = os.path.dirname(self.kernel)


    @error.context_aware
    def render_answer_file(self):
        """
        Replace KVM_TEST_CDKEY (in the unattended file) with the cdkey
        provided for this test and replace the KVM_TEST_MEDIUM with
        the tree url or nfs address provided for this test.

        @return: Answer file contents
        """
        error.base_context('Rendering final answer file')
        error.context('Reading answer file %s' % self.unattended_file)
        unattended_contents = open(self.unattended_file).read()
        dummy_cdkey_re = r'\bKVM_TEST_CDKEY\b'
        real_cdkey = os.environ.get('KVM_TEST_cdkey')
        if re.search(dummy_cdkey_re, unattended_contents):
            if real_cdkey:
                unattended_contents = re.sub(dummy_cdkey_re, real_cdkey,
                                             unattended_contents)
            else:
                print ("WARNING: 'cdkey' required but not specified for "
                       "this unattended installation")

        dummy_medium_re = r'\bKVM_TEST_MEDIUM\b'
        if self.medium == "cdrom":
            content = "cdrom"
        elif self.medium == "url":
            content = "url --url %s" % self.url
        elif self.medium == "nfs":
            content = "nfs --server=%s --dir=%s" % (self.nfs_server,
                                                    self.nfs_dir)
        else:
            raise ValueError("Unexpected installation medium %s" % self.url)

        unattended_contents = re.sub(dummy_medium_re, content,
                                     unattended_contents)

        def replace_virtio_key(contents, dummy_re, env):
            """
            Replace a virtio dummy string with contents.

            If install_virtio is not set, replace it with a dummy string.

            @param contents: Contents of the unattended file
            @param dummy_re: Regular expression used to search on the.
                    unattended file contents.
            @param env: Name of the environment variable.
            """
            dummy_path = "C:"
            driver = os.environ.get(env, '')

            if re.search(dummy_re, contents):
                if self.install_virtio == "yes":
                    if driver.endswith("msi"):
                        driver = 'msiexec /passive /package ' + driver
                    else:
                        try:
                            # Let's escape windows style paths properly
                            drive, path = driver.split(":")
                            driver = drive + ":" + re.escape(path)
                        except:
                            pass
                    contents = re.sub(dummy_re, driver, contents)
                else:
                    contents = re.sub(dummy_re, dummy_path, contents)
            return contents

        vdict = {r'\bKVM_TEST_STORAGE_DRIVER_PATH\b':
                 'KVM_TEST_virtio_storage_path',
                 r'\bKVM_TEST_NETWORK_DRIVER_PATH\b':
                 'KVM_TEST_virtio_network_path',
                 r'\bKVM_TEST_VIRTIO_NETWORK_INSTALLER\b':
                 'KVM_TEST_virtio_network_installer_path'}

        for vkey in vdict:
            unattended_contents = replace_virtio_key(unattended_contents,
                                                     vkey, vdict[vkey])

        logging.debug("Unattended install contents:")
        for line in unattended_contents.splitlines():
            logging.debug(line)
        return unattended_contents


    def setup_boot_disk(self):
        answer_contents = self.render_answer_file()

        if self.unattended_file.endswith('.sif'):
            dest_fname = 'winnt.sif'
            setup_file = 'winnt.bat'
            boot_disk = FloppyDisk(self.floppy, self.qemu_img_binary,
                                   self.tmpdir)
            boot_disk.setup_answer_file(dest_fname, answer_contents)
            setup_file_path = os.path.join(self.unattended_dir, setup_file)
            boot_disk.copy_to(setup_file_path)
            if self.install_virtio == "yes":
                boot_disk.setup_virtio_win2003(self.virtio_floppy,
                                               self.virtio_oemsetup_id)
            boot_disk.copy_to(self.finish_program)

        elif self.unattended_file.endswith('.ks'):
            # Red Hat kickstart install
            dest_fname = 'ks.cfg'
            if self.cdrom_unattended:
                boot_disk = CdromDisk(self.cdrom_unattended, self.tmpdir)
            elif self.floppy:
                boot_disk = FloppyDisk(self.floppy, self.qemu_img_binary,
                                       self.tmpdir)
            else:
                raise ValueError("Neither cdrom_unattended nor floppy set "
                                 "on the config file, please verify")
            boot_disk.setup_answer_file(dest_fname, answer_contents)

        elif self.unattended_file.endswith('.xml'):
            if "autoyast" in self.extra_params:
                # SUSE autoyast install
                dest_fname = "autoinst.xml"
                if self.cdrom_unattended:
                    boot_disk = CdromDisk(self.cdrom_unattended)
                elif self.floppy:
                    boot_disk = FloppyDisk(self.floppy, self.qemu_img_binary,
                                           self.tmpdir)
                else:
                    raise ValueError("Neither cdrom_unattended nor floppy set "
                                     "on the config file, please verify")
                boot_disk.setup_answer_file(dest_fname, answer_contents)

            else:
                # Windows unattended install
                dest_fname = "autounattend.xml"
                boot_disk = FloppyDisk(self.floppy, self.qemu_img_binary,
                                       self.tmpdir)
                boot_disk.setup_answer_file(dest_fname, answer_contents)
                if self.install_virtio == "yes":
                    boot_disk.setup_virtio_win2008(self.virtio_floppy)
                boot_disk.copy_to(self.finish_program)

        else:
            raise ValueError('Unknown answer file type: %s' %
                             self.unattended_file)

        boot_disk.close()


    @error.context_aware
    def setup_cdrom(self):
        """
        Mount cdrom and copy vmlinuz and initrd.img.
        """
        error.context("Copying vmlinuz and initrd.img from install cdrom %s" %
                      self.cdrom_cd1)
        m_cmd = ('mount -t iso9660 -v -o loop,ro %s %s' %
                 (self.cdrom_cd1, self.cdrom_cd1_mount))
        utils.run(m_cmd)

        try:
            if not os.path.isdir(self.image_path):
                os.makedirs(self.image_path)
            kernel_fetch_cmd = ("cp %s/%s/%s %s" %
                                (self.cdrom_cd1_mount, self.boot_path,
                                 os.path.basename(self.kernel), self.kernel))
            utils.run(kernel_fetch_cmd)
            initrd_fetch_cmd = ("cp %s/%s/%s %s" %
                                (self.cdrom_cd1_mount, self.boot_path,
                                 os.path.basename(self.initrd), self.initrd))
            utils.run(initrd_fetch_cmd)
        finally:
            cleanup(self.cdrom_cd1_mount)


    @error.context_aware
    def setup_url(self):
        """
        Download the vmlinuz and initrd.img from URL.
        """
        error.context("downloading vmlinuz and initrd.img from %s" % self.url)
        os.chdir(self.image_path)
        kernel_fetch_cmd = "wget -q %s/%s/%s" % (self.url, self.boot_path,
                                                 os.path.basename(self.kernel))
        initrd_fetch_cmd = "wget -q %s/%s/%s" % (self.url, self.boot_path,
                                                 os.path.basename(self.initrd))

        if os.path.exists(self.kernel):
            os.remove(self.kernel)
        if os.path.exists(self.initrd):
            os.remove(self.initrd)

        utils.run(kernel_fetch_cmd)
        utils.run(initrd_fetch_cmd)


    def setup_nfs(self):
        """
        Copy the vmlinuz and initrd.img from nfs.
        """
        error.context("copying the vmlinuz and initrd.img from NFS share")

        m_cmd = ("mount %s:%s %s -o ro" %
                 (self.nfs_server, self.nfs_dir, self.nfs_mount))
        utils.run(m_cmd)

        try:
            kernel_fetch_cmd = ("cp %s/%s/%s %s" %
                                (self.nfs_mount, self.boot_path,
                                os.path.basename(self.kernel), self.image_path))
            utils.run(kernel_fetch_cmd)
            initrd_fetch_cmd = ("cp %s/%s/%s %s" %
                                (self.nfs_mount, self.boot_path,
                                os.path.basename(self.initrd), self.image_path))
            utils.run(initrd_fetch_cmd)
        finally:
            cleanup(self.nfs_mount)


    def setup(self):
        """
        Configure the environment for unattended install.

        Uses an appropriate strategy according to each install model.
        """
        logging.info("Starting unattended install setup")

        logging.debug("Variables set:")
        for member in inspect.getmembers(self):
            name, value = member
            attribute = getattr(self, name)
            if not (name.startswith("__") or callable(attribute) or not value):
                logging.debug("    %s: %s", name, value)

        if self.unattended_file and (self.floppy or self.cdrom_unattended):
            self.setup_boot_disk()
        if self.medium == "cdrom":
            if self.kernel and self.initrd:
                self.setup_cdrom()
        elif self.medium == "url":
            self.setup_url()
        elif self.medium == "nfs":
            self.setup_nfs()
        else:
            raise ValueError("Unexpected installation method %s" %
                             self.medium)


class HugePageConfig(object):
    def __init__(self, params):
        """
        Gets environment variable values and calculates the target number
        of huge memory pages.

        @param params: Dict like object containing parameters for the test.
        """
        self.vms = len(params.objects("vms"))
        self.mem = int(params.get("mem"))
        self.max_vms = int(params.get("max_vms", 0))
        self.hugepage_path = '/mnt/kvm_hugepage'
        self.hugepage_size = self.get_hugepage_size()
        self.target_hugepages = self.get_target_hugepages()
        self.kernel_hp_file = '/proc/sys/vm/nr_hugepages'


    def get_hugepage_size(self):
        """
        Get the current system setting for huge memory page size.
        """
        meminfo = open('/proc/meminfo', 'r').readlines()
        huge_line_list = [h for h in meminfo if h.startswith("Hugepagesize")]
        try:
            return int(huge_line_list[0].split()[1])
        except ValueError, e:
            raise ValueError("Could not get huge page size setting from "
                             "/proc/meminfo: %s" % e)


    def get_target_hugepages(self):
        """
        Calculate the target number of hugepages for testing purposes.
        """
        if self.vms < self.max_vms:
            self.vms = self.max_vms
        # memory of all VMs plus qemu overhead of 64MB per guest
        vmsm = (self.vms * self.mem) + (self.vms * 64)
        return int(vmsm * 1024 / self.hugepage_size)


    @error.context_aware
    def set_hugepages(self):
        """
        Sets the hugepage limit to the target hugepage value calculated.
        """
        error.context("setting hugepages limit to %s" % self.target_hugepages)
        hugepage_cfg = open(self.kernel_hp_file, "r+")
        hp = hugepage_cfg.readline()
        while int(hp) < self.target_hugepages:
            loop_hp = hp
            hugepage_cfg.write(str(self.target_hugepages))
            hugepage_cfg.flush()
            hugepage_cfg.seek(0)
            hp = int(hugepage_cfg.readline())
            if loop_hp == hp:
                raise ValueError("Cannot set the kernel hugepage setting "
                                 "to the target value of %d hugepages." %
                                 self.target_hugepages)
        hugepage_cfg.close()
        logging.debug("Successfuly set %s large memory pages on host ",
                      self.target_hugepages)


    @error.context_aware
    def mount_hugepage_fs(self):
        """
        Verify if there's a hugetlbfs mount set. If there's none, will set up
        a hugetlbfs mount using the class attribute that defines the mount
        point.
        """
        error.context("mounting hugepages path")
        if not os.path.ismount(self.hugepage_path):
            if not os.path.isdir(self.hugepage_path):
                os.makedirs(self.hugepage_path)
            cmd = "mount -t hugetlbfs none %s" % self.hugepage_path
            utils.system(cmd)


    def setup(self):
        logging.debug("Number of VMs this test will use: %d", self.vms)
        logging.debug("Amount of memory used by each vm: %s", self.mem)
        logging.debug("System setting for large memory page size: %s",
                      self.hugepage_size)
        logging.debug("Number of large memory pages needed for this test: %s",
                      self.target_hugepages)
        self.set_hugepages()
        self.mount_hugepage_fs()


    @error.context_aware
    def cleanup(self):
        error.context("trying to dealocate hugepage memory")
        try:
            utils.system("umount %s" % self.hugepage_path)
        except error.CmdError:
            return
        utils.system("echo 0 > %s" % self.kernel_hp_file)
        logging.debug("Hugepage memory successfuly dealocated")