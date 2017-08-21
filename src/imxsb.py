#!/usr/bin/env python

import os
import sys
import imx
import yaml
import uboot
import click


########################################################################################################################
## SmartBoot: Booting Data container
########################################################################################################################

class DatSegBase(object):

    @property
    def name(self):
        return self._name

    @property
    def description(self):
        return self._desc

    @property
    def address(self):
        return self._addr

    @property
    def data(self):
        if self._data is None:
            with open(self._path, 'r' if self._path.lower().endswith('.txt') else 'rb') as f:
                self._data = f.read()

        if type(self._data) is str:
            self._data = self._export(self._data)

        return self._data

    def __init__(self, name, desc='', addr=None, path=None, data=None):
        self._name = name
        self._desc = desc
        self._addr = addr
        self._path = path
        self._data = data

    def _export(self, txt_data):
        return txt_data


class DatSegIMX(DatSegBase):

    @property
    def data(self):
        if self._data is None:
            with open(self._path, 'rb') as f:
                d = f.read()

            if self._eval is not None and self._mode != 'disabled':
                d = self._update_env(d)
        else:
            img = imx.BootImage(address = self._data['STADDR'], offset = self._data['OFFSET'])
            img.app = self._data['APPSEG'].img
            img.dcd = self._data['DCDSEG'].get()
            d = img.export()

        return d

    def __init__(self, name, desc='', addr=None, path=None, data=None, eval=None, mark='bootdelay=', mode='DISABLED'):
        super().__init__(name, desc, addr, path, data)
        self._eval = eval
        self._mark = mark
        self._mode = mode.islower()

    def _update_env(self, data):
        env = uboot.EnvImgOld(self._mark)
        env.import_img(data)
        if self._mode == 'replace':
            env.clear()
        env.load(self._eval)
        return env.export_img()

    def get_ivt_address(self):
        img = imx.BootImage()
        img.parse(self.data)
        return img.address + img.offset

    def get_dcd_data(self):
        img = imx.BootImage()
        img.parse(self.data)
        return img.dcd.export()


class DatSegDCD(DatSegBase):

    def _export(self, txt_data):
        dcd = imx.SegDCD()
        dcd.load(txt_data)
        return dcd.export()

    def get(self):
        dcd = imx.SegDCD()

        if self._data is None:
            with open(self._path, 'r' if self._path.lower().endswith('.txt') else 'rb') as f:
                self._data = f.read()

        if type(self._data) is str:
            dcd.load(self._data)
        else:
            dcd.parse(self._data)

        return dcd


class DatSegUST(DatSegBase):

    def _export(self, txt_data):
        simg = uboot.ScriptImage()
        simg.load(txt_data)
        return simg.export()


class DatSegUFW(DatSegBase):

    @property
    def data(self):
        if self._data is None:
            with open(self._path, 'rb') as f:
                self._data = f.read()

        if self._eval is not None and self._mode != 'disabled':
            self._data = self._update_env(self._data)

        return self._data

    def __init__(self, name, desc='', addr=None, path=None, data=None, eval=None, mark='bootdelay=', mode='DISABLED'):
        super().__init__(name, desc, addr, path, data)
        self._eval = eval
        self._mark = mark
        self._mode = mode.islower()

    def _update_env(self, data):
        env = uboot.EnvImgOld(self._mark)
        env.import_img(data)
        if self._mode == 'replace':
            env.clear()
        env.load(self._eval)
        return env.export_img()


class DatSegBIN(DatSegBase):

    @property
    def data(self):
        if self._data is None:
            with open(self._path, 'rb') as f:
                self._data = f.read()

        return self._data


########################################################################################################################
## SmartBoot: Config File Manager
########################################################################################################################

class SMX(object):

    @property
    def name(self):
        return self._name

    @property
    def target(self):
        return self._target

    @property
    def description(self):
        return self._desc

    def __init__(self):
        self._name = None
        self._desc = None
        self._target = None

        self._data = []
        self._body = []

        self._path = None

    def get_data(self, name):
        ret = None

        for d in self._data:
            if d.name == name:
                ret = d
                break

        return ret

    def get_script(self, index):
        assert index < len(self._body), "Index out of range !"

        line_cnt = 0
        txt_data = self._body[index]['CMDS']
        script = []

        for line in txt_data.split('\n'):
            line = line.rstrip('\0')
            # increment line counter
            line_cnt += 1
            # ignore comments
            if not line or line.startswith('#'):
                continue

            line = line.split()

            if line[0] == 'SDCD':
                cmd = {'NAME': 'SDCD', 'DESC': 'Skip DCD Segment in IMX image'}

            elif line[0] == 'JRUN':
                assert len(line) == 2

                try:
                    addr = int(line[1], 10)
                except:
                    dseg = self.get_data(line[1])
                    addr = dseg.get_ivt_address()

                if addr is None:
                    raise Exception("Address not defined")

                cmd = {'NAME': 'JRUN', 'ADDR': addr, 'DESC': 'Start Boot ...'}

            elif line[0] == 'WREG':
                assert len(line) == 4

                bts  = int(line[1], 10)
                addr = int(line[2], 0)
                val  = int(line[3], 0)

                cmd = {'NAME': 'WREG', 'BYTES': bts, 'ADDR': addr, 'VALUE': val,
                       'DESC': 'Write: REG[0x{0:08X}] = 0x{1:08X}'.format(addr, val)}

            elif line[0] == 'WDCD':
                dseg = self.get_data(line[1])
                if dseg is None:
                    raise Exception("Data not defined")

                addr = int(line[2], 0) if len(line) == 3 else None

                if type(dseg) is DatSegDCD:
                    data = dseg.data
                    if addr is None:
                        addr = dseg.address
                else:
                    data = dseg.get_dcd_data()

                if addr is None:
                    raise Exception("Address not defined")

                cmd = {'NAME': 'WDCD', 'ADDR': addr, 'DATA': data, 'DESC': 'Write {}'.format(dseg.description)}

            elif line[0] == 'WIMG':
                dseg = self.get_data(line[1])
                if dseg is None:
                    raise Exception("Data not defined")

                addr = int(line[2], 0) if len(line) == 3 else dseg.address
                if type(dseg) is DatSegIMX and addr is None:
                    addr = dseg.get_ivt_address()

                if addr is None:
                    raise Exception("Address not defined")

                cmd = {'NAME': 'WIMG', 'ADDR': addr, 'DATA': dseg.data, 'DESC': 'Write {}'.format(dseg.description)}

            else:
                raise Exception("Unsupported command: %s" % line[0])

            script.append(cmd)

        return script

    def open(self, yaml_file):
        # set path variable
        self._path = os.path.abspath(os.path.dirname(yaml_file))

        # load yaml_file
        yaml_data  = yaml.load(open(yaml_file))

        # validate segments in file
        if not 'HEAD' in yaml_data:
            raise Exception("HEAD segment doesnt exist inside file: %s" % yaml_file)

        if not 'DATA' in yaml_data:
            raise Exception("DATA segment doesnt exist inside file: %s" % yaml_file)

        if not 'BODY' in yaml_data:
            raise Exception("BODY segment doesnt exist inside file: %s" % yaml_file)

        # parse head
        self._name   = yaml_data['HEAD']['NAME']
        self._desc   = yaml_data['HEAD']['DESC']
        self._target = yaml_data['HEAD']['CHIP']

        # parse data
        for name, dseg in yaml_data["DATA"].items():

            desc = dseg['DESC'] if 'DESC' in dseg else ""
            addr = dseg['ADDR'] if 'ADDR' in dseg else None
            type = dseg['TYPE'] if 'TYPE' in dseg else 'BIN'
            data = dseg['DATA'] if 'DATA' in dseg else None
            eval = dseg['EVAL'] if 'EVAL' in dseg else None
            mark = dseg['MARK'] if 'MARK' in dseg else 'bootdelay='
            mode = dseg['MODE'] if 'MODE' in dseg else 'DISABLED'
            path = None

            if 'FILE' in dseg:
                for abs_path in [dseg['FILE'], os.path.join(self._path, dseg['FILE'])]:
                    abs_path = os.path.normpath(abs_path)
                    if os.path.exists(abs_path):
                        path = abs_path
                        break

                if path is None:
                    raise Exception("The file: %s doesnt exist" % dseg['FILE'])

                desc = '{} ({})'.format(desc, dseg['FILE'])

            if data is not None and isinstance(data, dict):
                data['DCDSEG'] = self.get_data(data['DCDSEG'])
                data['APPSEG'] = self.get_data(data['APPSEG'])

            if path is None and data is None:
                raise Exception("The path/data must be defined for %s segment" % name)

            if   type == 'IMX':
                self._data.append(DatSegIMX(name, desc, addr, path, data, eval, mark, mode))
            elif type == 'DCD':
                self._data.append(DatSegDCD(name, desc, addr, path, data))
            elif type == 'UFW':
                self._data.append(DatSegUFW(name, desc, addr, path, data, eval, mark, mode))
            elif type == 'UST':
                self._data.append(DatSegUST(name, desc, addr, path, data))
            else:
                self._data.append(DatSegBIN(name, desc, addr, path, data))

        # load scripts
        if yaml_data["BODY"]:
            self._body = yaml_data["BODY"]
        else:
            raise Exception("BODY segment is empty inside file: %s" % yaml_file)

    def list(self):
        return [(script['NAME'], script['DESC'] if 'DESC' in script else '-') for script in self._body]

    def count(self):
        return len(self._body)


########################################################################################################################
## SmartBoot: Command Line Interface
########################################################################################################################

# Application error code
ERROR_CODE = 1

# Application version
VERSION = imx.__version__

# Application description
DESCRIP = (
    "IMX Smart Boot, ver.: " + VERSION + " Beta\n\n"
    "NOTE: Development version, be carefully with it usage !\n"
)


# Base options
@click.group(context_settings=dict(help_option_names=['-?', '--help']), help=DESCRIP)
@click.argument('file', nargs=1, type=click.Path(exists=True))
@click.version_option(VERSION, '-v', '--version')
@click.pass_context
def cli(ctx, file):
    # ...
    smx = SMX()
    # open and load data file
    try:
        smx.open(file)
    except Exception as e:
        click.secho("Error: %s\n" % str(e))
        sys.exit(ERROR_CODE)
    # ...
    ctx.obj['SMX'] = smx

    click.echo()


# info command
@cli.command(short_help="Get data file info")
@click.pass_context
def info(ctx):
    smx = ctx.obj['SMX']
    num = 0
    #click.echo(" ")
    for name, desc in smx.list():
        click.secho("%d) %s (%s)" % (num, name, desc))
        num += 1


# run command
@cli.command(short_help="Start Controlled Boot")
@click.option('-s', '--sid', type=click.INT, help="Script index")
@click.pass_context
def run(ctx, sid=None):
    smx = ctx.obj['SMX']
    #try:
    # get target USB PID
    pid = imx.SerialDownloader.HID_PID[smx.target]
    # scan for USB target
    devs = imx.SerialDownloader.scanUSB(pid)
    if not devs:
        raise Exception("%s device not connected !" % smx.target)
    # select boot script
    if sid is None or sid > smx.count():
        num = 0
        #click.echo(" ")
        for name, desc in smx.list():
            click.secho("%d) %s (%s)" % (num, name, desc))
            num += 1
        click.echo("\nUse: ", nl=False)
        c = input()
        sid = int(c, 10)
        click.echo()
    # load script
    script = smx.get_script(sid)
    # connect target
    flasher = imx.SerialDownloader()
    flasher.connectUSB(devs[0])
    # execute script
    num = 1
    for cmd in script:
        # print command info
        click.secho("%d/%d) %s" % (num, len(script), cmd['DESC']))

        if cmd['NAME'] == 'WREG':
            flasher.write(cmd['ADDR'], cmd['VALUE'], cmd['BYTES'])

        elif cmd['NAME'] == 'WDCD':
            flasher.writeDCD(cmd['ADDR'], cmd['DATA'])

        elif cmd['NAME'] == 'WIMG':
            flasher.writeFile(cmd['ADDR'], cmd['DATA'])

        elif cmd['NAME'] == 'SDCD':
            flasher.skipDCD()

        elif cmd['NAME'] == 'JRUN':
            flasher.jumpAndRun(cmd['ADDR'])

        else:
            raise Exception("Command: %s not defined" % cmd['NAME'])

        num += 1

    # disconnect target
    flasher.disconnect()


def main():
    cli(obj={})


if __name__ == '__main__':
    main()
