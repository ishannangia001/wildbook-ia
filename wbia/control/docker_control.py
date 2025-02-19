# -*- coding: utf-8 -*-
import logging
from wbia.control import controller_inject
import utool as ut
import time

(print, rrr, profile) = ut.inject2(__name__)
logger = logging.getLogger('wbia')


try:
    import docker

    assert docker.from_env() is not None
except Exception:
    logger.warning('Local docker client is not available')


_, register_ibs_method = controller_inject.make_ibs_register_decorator(__name__)


DOCKER_CONFIG_REGISTRY = {}
DOCKER_IMAGE_PREFIX = [
    'wildme',
]
DOCKER_DEFAULT_RUN_ARGS = {'detach': True, 'restart_policy': {'Name': 'on-failure'}}


DOCKER_CLONE_FMTSTR = '%s_clone_%d'


@register_ibs_method
def docker_container_clone_name(container_name, clone=None):
    if clone is None:
        return container_name
    container_clone_name = DOCKER_CLONE_FMTSTR % (container_name, clone)
    return container_clone_name


@register_ibs_method
# don't rely on the ibs object in this method; needs to be callable on import
# container_check_func takes a url and returns a boolean
def docker_register_config(
    ibs,
    container_name,
    image_name,
    container_check_func=None,
    run_args={},
    ensure_new=False,
):
    if container_name in DOCKER_CONFIG_REGISTRY:
        if ensure_new:
            raise RuntimeError(
                'Container name has already been added to the config registry'
            )
        else:
            logger.info(
                'Warning: docker_register_config called on an existing config. Already have container named %s'
                % (container_name,)
            )
    valid = False
    for prefix in DOCKER_IMAGE_PREFIX:
        if image_name.startswith(prefix):
            valid = True
            break
    if not valid:
        raise RuntimeError(
            'Cannot register an image name that does not have a prefix in %r'
            % (DOCKER_IMAGE_PREFIX,)
        )
    DOCKER_CONFIG_REGISTRY[container_name] = {
        'image': image_name,
        'run_args': run_args,
        'container_check_func': container_check_func,
    }


def is_local_port_open(port):
    """
    Args:
        port (int):

    Returns:
        bool:

    References:
        http://stackoverflow.com/questions/7436801/identifying-listening-ports-using-python

    CommandLine:
        python -m utool.util_web is_local_port_open --show

    Example:
        >>> # DISABLE_DOCTEST
        >>> from utool.util_web import *  # NOQA
        >>> port = 32183
        >>> assert is_local_port_open(80) is False, 'port 80 should always be closed'
        >>> assert is_local_port_open(port) is True, 'maybe this port is actually used?'
    """
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result1 = s.connect_ex(('localhost', port))
    result2 = s.connect_ex(('127.0.0.1', port))
    result3 = s.connect_ex(('0.0.0.0', port))  # Support for Docker networking
    s.close()
    return (result1 != 0 and result2 != 0) or (result3 != 0)


def find_open_port(base=5000, blacklist=[]):
    port = base
    while port in blacklist or not is_local_port_open(port):
        port += 1
    return port


@register_ibs_method
# runs an image, returns url to container
def docker_run(
    ibs, image_name, container_name, override_run_args, clone=None, ensure_new=False
):
    if '_external_suggested_port' not in override_run_args:
        override_run_args['_external_suggested_port'] = 5000
    assert '_external_suggested_port' in override_run_args
    assert '_internal_port' in override_run_args

    blacklist_ports = []
    container_dict = ibs.docker_container_status_dict()
    for status in container_dict:
        for container_name_ in container_dict[status]:
            container_ = ibs.docker_get_container(container_name_)
            option_list = ibs.docker_container_IP_port_options(container_)
            print(container_name_, option_list)
            for option in option_list:
                url, port = option
                if port is not None:
                    port = int(port)
                    blacklist_ports.append(port)
    blacklist_ports = sorted(list(set(blacklist_ports)))

    ext_port = find_open_port(
        override_run_args['_external_suggested_port'], blacklist_ports
    )
    port_key = '%d/tcp' % (override_run_args['_internal_port'],)

    # remove underscore args from run_args
    key_list = list(override_run_args.keys())
    for key in key_list:
        if key.startswith('_'):
            override_run_args.pop(key)
    # update default args with run_args
    run_args = DOCKER_DEFAULT_RUN_ARGS.copy()
    run_args.update(override_run_args)
    # add other args
    run_args['ports'] = {port_key: ext_port}
    container_clone_name = docker_container_clone_name(container_name, clone=clone)
    run_args['name'] = container_clone_name
    logger.info(
        "We're starting image_name %s as %s with args %r"
        % (image_name, container_clone_name, run_args)
    )
    docker_client = docker.from_env()
    try:
        container = docker_client.containers.run(image_name, **run_args)
    except docker.errors.APIError as ex:
        if ensure_new:
            raise ex
        # get the container that's already running
        container = ibs.docker_get_container(container_name, clone=clone)

    # h/t https://github.com/docker/docker-py/issues/2128
    container.reload()

    docker_get_config = ibs.docker_get_config(container_name)
    url_list = ibs.docker_container_urls(container, docker_get_config)

    return url_list


@register_ibs_method
def docker_image_list(ibs):
    tag_list = []
    docker_client = docker.from_env()
    for image in docker_client.images.list():
        tag_list += image.tags
    tag_list = sorted(list(set(tag_list)))
    return tag_list


@register_ibs_method
def docker_get_image(ibs, image_name):
    docker_client = docker.from_env()
    for image in docker_client.images.list():
        if image_name in image.tags:
            return image
    return None


# TODO download the image from remote server
@register_ibs_method
def docker_ensure_image(ibs, image_name):
    image = ibs.docker_get_image(image_name)
    if image is None:
        image = ibs.docker_pull_image(image_name)
    return image


@register_ibs_method
def docker_pull_image(ibs, image_name):
    u"""
    The process of logging into the Azure Container Registry is arcane and complex.
    In the meantime we'll assume that any image we need in the ACR has been downloaded
    by a logged-in user.


    Host: example.azurecr.io
    Username: example@example.com
    Password: asecurepassword

    Login Script:

    Install Azure CLI

    https://docs.microsoft.com/en-us/cli/azure/install-azure-cli?view=azure-cli-latest

    az logout

    Logout of any user you may already be using with az-cli

    az login

    Follow instructions to https://microsoft.com/devicelogin, input the code

    Login as example@example.com with password above

    az acr login --name wildme

    Login to the Azure Container Registry (ACR) for Docker

    Verify login with “cat ~/.docker/config.json | jq ".auths" and look for example.azurecr.io”

    docker pull example.azurecr.io/wbia/example-image:latest

    Pull latest nightly image
    """
    raise NotImplementedError


@register_ibs_method
def docker_login(ibs):
    # login to the ACR with credentials from "somewhere"
    raise NotImplementedError


@register_ibs_method
def docker_image_run(ibs, port=6000, volumes=None):
    raise NotImplementedError


@register_ibs_method
def docker_container_status_dict(ibs):
    container_dict = {}
    docker_client = docker.from_env()
    for container in docker_client.containers.list():
        if container.status not in container_dict:
            container_dict[container.status] = []
        container_dict[container.status].append(container.name)
    for status in container_dict:
        container_dict[status] = sorted(list(set(container_dict[status])))
    return container_dict


@register_ibs_method
def docker_container_status(ibs, container_name, clone=None):
    container_dict = ibs.docker_container_status_dict()
    clone_name = docker_container_clone_name(container_name, clone)
    for status in container_dict:
        if clone_name in container_dict[status]:
            return status
    return None


@register_ibs_method
def docker_container_IP_port_options(ibs, container):
    networksettings = container.attrs['NetworkSettings']

    option_list = []

    networks = networksettings['Networks']
    for network in networks:
        ipaddress = networks[network]['IPAddress']
        option = (ipaddress, None)
        option_list.append(option)

    # TODO: understand the container.attrs['NetworkSettings']['Ports']: a dict (??) of lists (??) of dicts (only one '?')
    ports = networksettings['Ports']
    for key in ports:
        # ports is a dict keyed by e.g. '5000/tcp'. This is the only key I've seen so far on our containers.
        dict_list = ports[key]
        if dict_list is None:
            continue
        for dict_ in dict_list:
            # ports[key] is a list of dicts
            if 'HostPort' in dict_:
                # just return the first HostPort we find. Doesn't seem right... but what better logic?
                option = (
                    dict_['HostIp'],
                    dict_['HostPort'],
                )
                option_list.append(option)

    # should we throw an assert/error here?
    return option_list


@register_ibs_method
def docker_container_urls_from_name(ibs, container_name, clone=None):
    if ibs.docker_container_status(container_name, clone=clone) != 'running':
        return None
    container = ibs.docker_get_container(container_name, clone=clone)
    docker_get_config = ibs.docker_get_config(container_name)
    url_list = ibs.docker_container_urls(container, docker_get_config)
    return url_list


@register_ibs_method
def docker_container_urls(ibs, container, docker_get_config):
    _internal_port = docker_get_config.get('run_args', {}).get('_internal_port', None)
    logger.info('[docker_container_urls] Found _internal_port: %s' % (_internal_port,))
    option_list = ibs.docker_container_IP_port_options(container)
    url_list = []
    for option in option_list:
        ip, port = option
        if port is None:
            # Try to use internal port, if known
            port = _internal_port
        if port is None:
            url = '%s' % (ip,)
        else:
            url = '%s:%s' % (ip, port)
        url_list.append(url)
    return url_list


@register_ibs_method
def docker_get_container(ibs, container_name, clone=None):
    container_clone_name = docker_container_clone_name(container_name, clone)
    docker_client = docker.from_env()
    for container in docker_client.containers.list():
        if container.name == container_clone_name:
            return container
    return None


@register_ibs_method
def docker_ensure(ibs, container_name, check_container=True, clone=None):
    config = ibs.docker_get_config(container_name)

    # Check for container in running containers
    if ibs.docker_container_status(container_name, clone=clone) != 'running':
        image_name = config['image']
        ibs.docker_ensure_image(image_name)
        run_args = config['run_args'].copy()
        ibs.docker_run(image_name, container_name, run_args, clone=clone)

    original_url_list = ibs.docker_container_urls_from_name(container_name, clone=clone)
    # If not, check if the image has been downloaded from the config

    if check_container:
        valid_url_list = ibs.docker_check_container(container_name, clone=clone)
        assert len(valid_url_list) > 0, 'Could not validate container'
        return valid_url_list
    else:
        return original_url_list


@register_ibs_method
def docker_check_container(
    ibs, container_name, clone=None, retry_count=20, retry_timeout=15
):
    config = ibs.docker_get_config(container_name)
    check_func = config['container_check_func']
    url_list = ibs.docker_container_urls_from_name(container_name, clone=clone)
    if check_func is None:
        return url_list
    valid_url_list = []
    for retry_index in range(retry_count):
        logger.info(
            '[docker_control] Performing container check (attempt %d, max %d)'
            % (retry_index + 1, retry_count)
        )
        for url in url_list:
            logger.info('\tChecking URL: %s' % (url,))
            if check_func(url):
                valid_url_list.append(url)
        if len(valid_url_list) > 0:
            break
        logger.info(
            '[docker_control] ERROR!  Container failed the plugin-defined check, sleeping for %d seconds and will try again'
            % (retry_timeout,)
        )
        time.sleep(retry_timeout)
    return valid_url_list


@register_ibs_method
def docker_get_config(ibs, container_name):
    config = DOCKER_CONFIG_REGISTRY.get(container_name, None)
    message = "The container name '%s' has not been registered" % container_name
    assert config is not None, message
    return config


# @register_ibs_method
# def docker_embed(ibs):
#     ut.embed()
